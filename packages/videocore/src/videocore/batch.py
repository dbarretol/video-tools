"""Batch processing of many videos with per-item error isolation."""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# How often a parallel run checks `should_cancel` while workers are busy.
CANCEL_POLL_SECONDS = 0.2

VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".mpg", ".mpeg"}
)
# stat.FILE_ATTRIBUTE_* exist only on Windows builds of Python.
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4
# Offline, recall on open, recall on data access: set on cloud-only (placeholder) files.
_FILE_ATTRIBUTES_OFFLINE = 0x1000 | 0x40000 | 0x400000


@dataclass
class BatchResult:
    item: Path
    ok: bool
    value: Any = None
    error: str | None = None
    skipped: bool = False
    """Not processed because its outputs already existed; `ok` is True."""
    detail: str | None = None
    """Why it was skipped, for apps that tell cases apart (e.g. ``"outdated"``)."""
    error_type: str | None = None
    """Exception class name of a failed item."""
    seconds: float | None = None
    """Time spent processing the item."""


def find_videos(
    folder: str | os.PathLike[str],
    *,
    recursive: bool = False,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
    skip_dir: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """List video files in `folder`, sorted by path.

    Hidden files and folders (dot names such as macOS ``._clip.mp4`` resource
    forks, or the Windows hidden attribute) are ignored. When `recursive`,
    subfolders for which `skip_dir(path)` returns True are not entered; apps use
    it to keep their own output folders out of the scan.
    """
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"Folder not found: {root}")
    suffixes = {ext.lower() for ext in extensions}
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if recursive:
            # Pruning in place keeps os.walk out of the skipped folders.
            dirnames[:] = [
                name
                for name in dirnames
                if not is_hidden(current / name)
                and not (skip_dir is not None and skip_dir(current / name))
            ]
        else:
            dirnames.clear()
        found += [
            current / name
            for name in filenames
            if Path(name).suffix.lower() in suffixes and not is_hidden(current / name)
        ]
    return sorted(found)


def is_hidden(path: Path) -> bool:
    """Dot names on any platform, plus the hidden or system attribute on Windows."""
    if path.name.startswith("."):
        return True
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM))


def is_offline(path: Path) -> bool:
    """True for files whose data is only in the cloud, such as OneDrive placeholders.

    Reading them makes Windows download them first. Always False elsewhere.
    """
    try:
        attributes = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _FILE_ATTRIBUTES_OFFLINE)


def default_workers() -> int:
    """Roughly one worker per physical core; decoders also use threads internally."""
    return max(1, (os.cpu_count() or 2) // 2)


def run_parallel(
    fn: Callable[..., Any],
    items: Sequence[Path],
    *,
    item_args: Sequence[Any] | None = None,
    workers: int | None = None,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Apply `fn` to every item, isolating failures, and return results in input order.

    With more than one worker `fn` runs in separate processes, so it must be a
    module-level (picklable) function. ``workers=1`` runs inline, which is easier
    to debug. `on_progress(done, total, result)` is called as each item finishes.

    With `item_args` (one value per item) `fn` is called as ``fn(item, arg)``.
    Each worker receives only the value of its own item, so per-item data such
    as output paths does not have to be bound into `fn` for every item.

    `should_cancel()` is polled a few times per second. When it returns True,
    or when this call is interrupted (e.g. Ctrl+C), worker processes are
    terminated right away instead of finishing their current item, so nothing
    keeps running in the background; unfinished items are left out of the
    results and their outputs may be incomplete. Inline runs (one worker) can
    only stop between items.

    Log records emitted in worker processes are forwarded to the handlers of
    this process's root logger, so they end up in the same log file and console.
    """
    workers = workers or default_workers()
    total = len(items)
    if item_args is not None and len(item_args) != total:
        raise ValueError("item_args must have one value per item")
    calls = [(item,) if item_args is None else (item, item_args[i]) for i, item in enumerate(items)]
    results: dict[int, BatchResult] = {}

    def record(index: int, result: BatchResult) -> None:
        results[index] = result
        if not result.ok:
            logger.warning("Failed to process %s: %s", result.item, result.error)
        if on_progress is not None:
            on_progress(len(results), total, result)

    if workers == 1 or total <= 1:
        for index, args in enumerate(calls):
            if should_cancel is not None and should_cancel():
                break
            record(index, _call(fn, *args))
        return [results[i] for i in sorted(results)]

    context = multiprocessing.get_context("spawn")
    with (
        _forward_worker_logs(context) as (initializer, initargs),
        ProcessPoolExecutor(
            max_workers=min(workers, total),
            mp_context=context,
            initializer=initializer,
            initargs=initargs,
        ) as pool,
    ):
        futures: dict[Future[BatchResult], int] = {
            pool.submit(_call, fn, *args): index for index, args in enumerate(calls)
        }
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(
                    pending, timeout=CANCEL_POLL_SECONDS, return_when=FIRST_COMPLETED
                )
                for future in done:
                    record(futures[future], future.result())
                if pending and should_cancel is not None and should_cancel():
                    logger.info("Cancelled: stopping %d unfinished items", len(pending))
                    break
        finally:
            if pending:
                _stop_workers(pool)
    return [results[i] for i in sorted(results)]


def _stop_workers(pool: ProcessPoolExecutor) -> None:
    """Drop queued items and kill running workers instead of waiting for them."""
    terminate = getattr(pool, "terminate_workers", None)  # public API from Python 3.14
    if terminate is not None:
        terminate()
        return
    # Grab the processes before shutdown(), which forgets them; otherwise they would
    # keep running and the interpreter would wait for them on exit.
    processes = list((pool._processes or {}).values())  # noqa: SLF001 - no public API before 3.14
    pool.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        process.terminate()
    for process in processes:
        process.join(timeout=5)


@contextmanager
def _forward_worker_logs(
    context: multiprocessing.context.SpawnContext,
) -> Iterator[tuple[Callable[..., None], tuple[Any, ...]]]:
    """Yield a pool initializer that sends worker log records to this process's handlers."""
    root = logging.getLogger()
    if not root.handlers:
        yield _init_worker, (None, root.level)
        return
    log_queue = context.Queue()
    listener = QueueListener(log_queue, *root.handlers, respect_handler_level=True)
    listener.start()
    try:
        yield _init_worker, (log_queue, root.level)
    finally:
        listener.stop()
        log_queue.close()


def _init_worker(log_queue: Any, level: int) -> None:
    # Ctrl+C reaches every process attached to the console. Workers ignore it so
    # the main process alone handles it: it terminates them and cleans up.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if log_queue is None:
        return
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(QueueHandler(log_queue))
    root.setLevel(level)


def _call(fn: Callable[..., Any], item: Path, *args: Any) -> BatchResult:
    started = time.perf_counter()
    try:
        value = fn(item, *args)
    except Exception as exc:  # noqa: BLE001 - one failing video must not abort the batch
        logger.debug("Details of the failure of %s", item, exc_info=True)
        return BatchResult(
            item=item,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            seconds=time.perf_counter() - started,
        )
    return BatchResult(item=item, ok=True, value=value, seconds=time.perf_counter() - started)
