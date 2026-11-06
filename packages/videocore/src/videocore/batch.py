"""Batch processing of many videos with per-item error isolation."""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import signal
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Any

from videocore import progress
from videocore.errors import hint_for
from videocore.progress import Cancelled, ProgressEvent

logger = logging.getLogger(__name__)

# How often a parallel run checks `should_cancel` while workers are busy.
CANCEL_POLL_SECONDS = 0.2
CANCEL_GRACE_SECONDS = 5.0
"""How long running items get to stop by themselves before their processes are killed."""

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
    cancelled: bool = False
    """Stopped by a cancel request before finishing; `ok` is False."""
    hint: str | None = None
    """What the user can do about the error (see ``videocore.errors.hint_for``)."""


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
    on_event: Callable[[ProgressEvent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Apply `fn` to every item, isolating failures, and return results in input order.

    With more than one worker `fn` runs in separate processes, so it must be a
    module-level (picklable) function. ``workers=1`` runs inline, which is easier
    to debug. `on_progress(done, total, result)` is called as each item finishes.

    With `item_args` (one value per item) `fn` is called as ``fn(item, arg)``.
    Each worker receives only the value of its own item, so per-item data such
    as output paths does not have to be bound into `fn` for every item.

    `on_event` receives the ``videocore.progress`` events of each item (started,
    stage progress). With worker processes they are delivered in this thread;
    inline they come from the thread that reports them, which may be a helper
    thread of the task.

    `should_cancel()` is polled a few times per second. When it returns True,
    items not started are dropped and running ones are asked to stop: tasks
    that call ``progress.check_cancelled`` stop at the next step, clean up and
    come back with ``cancelled=True``. Processes still busy after
    `CANCEL_GRACE_SECONDS` are killed. An interruption of this call (Ctrl+C)
    is handled the same way and then propagates. Dropped and killed items are
    left out of the results; their outputs may be incomplete.

    A worker process that dies does not abort the run: the items that were
    running are retried one at a time, and the one whose process dies again
    fails with ``error_type="BrokenProcessPool"`` (see `WORKER_LOST_HINT`).

    Log records emitted in worker processes are forwarded to the handlers of
    this process's root logger, so they end up in the same log file and console.
    """
    global _channel
    workers = workers or default_workers()
    total = len(items)
    if item_args is not None and len(item_args) != total:
        raise ValueError("item_args must have one value per item")
    calls = [(item,) if item_args is None else (item, item_args[i]) for i, item in enumerate(items)]
    results: dict[int, BatchResult] = {}

    def record(index: int, result: BatchResult) -> None:
        results[index] = result
        if result.cancelled:
            return
        if not result.ok:
            logger.warning("Failed to process %s: %s", result.item, result.error)
        if on_progress is not None:
            on_progress(len(results), total, result)

    if workers == 1 or total <= 1:
        previous, _channel = _channel, _Channel(on_event, should_cancel)
        try:
            for index, args in enumerate(calls):
                if should_cancel is not None and should_cancel():
                    break
                record(index, _call(fn, *args))
        finally:
            _channel = previous
        return [results[i] for i in sorted(results)]

    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    cancel = context.Event()
    started: set[Path] = set()

    def deliver_events() -> None:
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                return
            if event.kind == "started":
                started.add(event.item)
            if on_event is not None:
                on_event(event)

    def run_pool(indices: list[int], size: int, initializer: Any, initargs: Any) -> list[int]:
        """Run the items at `indices` on `size` workers; return those lost to a dead worker."""
        lost = []
        started.clear()
        with ProcessPoolExecutor(
            max_workers=min(size, len(indices)),
            mp_context=context,
            initializer=initializer,
            initargs=initargs,
        ) as pool:
            futures: dict[Future[BatchResult], int] = {
                pool.submit(_call, fn, *calls[index]): index for index in indices
            }
            pending = set(futures)
            try:
                while pending:
                    done, pending = wait(
                        pending, timeout=CANCEL_POLL_SECONDS, return_when=FIRST_COMPLETED
                    )
                    deliver_events()
                    for future in done:
                        try:
                            result = future.result()
                        except BrokenProcessPool:
                            lost.append(futures[future])
                        else:
                            record(futures[future], result)
                    if pending and should_cancel is not None and should_cancel():
                        logger.info("Cancelled: stopping %d unfinished items", len(pending))
                        break
            finally:
                if pending and _wind_down(futures, pending, cancel, record):
                    _stop_workers(pool)
                deliver_events()
        return sorted(lost)

    # A worker that dies (killed, out of memory, a crash in a decoder) breaks the
    # whole pool and every unfinished item comes back. Those not started run again
    # in a new pool; those that were running are suspects and run again one at a
    # time, so only the item that really kills its process fails. Every round
    # finishes or fails at least one item, so this ends.
    with _forward_worker_logs(context, events, cancel) as (initializer, initargs):
        todo, suspects = list(range(total)), []
        while (todo or suspects) and not cancel.is_set():
            if suspects:
                lost = run_pool(suspects, 1, initializer, initargs)
                crashed = [index for index in lost if items[index] in started] or lost[:1]
                for index in crashed:
                    record(index, _worker_lost(items[index]))
                suspects = [index for index in lost if index not in crashed]
                continue
            lost = run_pool(todo, workers, initializer, initargs)
            suspects = [index for index in lost if items[index] in started] or lost
            todo = [index for index in lost if index not in suspects]
            if lost:
                logger.warning(
                    "A worker process stopped unexpectedly; retrying %d %s one at a time",
                    len(suspects),
                    "video" if len(suspects) == 1 else "videos",
                )
    events.close()
    return [results[i] for i in sorted(results)]


WORKER_LOST_HINT = (
    "the process working on it was ended from outside or ran out of memory; run it again, "
    "with fewer videos in parallel (-w) if memory is short"
)


def _worker_lost(item: Path) -> BatchResult:
    return BatchResult(
        item=item,
        ok=False,
        error="Its worker process stopped unexpectedly",
        error_type="BrokenProcessPool",
        hint=WORKER_LOST_HINT,
    )


def _wind_down(
    futures: dict[Future[BatchResult], int],
    pending: set[Future[BatchResult]],
    cancel: Any,
    record: Callable[[int, BatchResult], None],
) -> set[Future[BatchResult]]:
    """Ask running items to stop and wait a little for them; return those still running."""
    cancel.set()
    pending = {future for future in pending if not future.cancel()}
    try:
        done, pending = wait(pending, timeout=CANCEL_GRACE_SECONDS)
    except KeyboardInterrupt:  # a second Ctrl+C: stop waiting
        return pending
    for future in done:
        if future.exception() is None:
            record(futures[future], future.result())
    return pending


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
    context: multiprocessing.context.SpawnContext, events: Any, cancel: Any
) -> Iterator[tuple[Callable[..., None], tuple[Any, ...]]]:
    """Yield a pool initializer that sends worker log records to this process's handlers.

    The initializer also hands each worker the progress queue and the cancel event.
    """
    root = logging.getLogger()
    if not root.handlers:
        yield _init_worker, (None, root.level, events, cancel)
        return
    log_queue = context.Queue()
    listener = QueueListener(log_queue, *root.handlers, respect_handler_level=True)
    listener.start()
    try:
        yield _init_worker, (log_queue, root.level, events, cancel)
    finally:
        listener.stop()
        log_queue.close()


@dataclass
class _Channel:
    """Where the items run by this process send their events and read cancel requests."""

    emit: Callable[[ProgressEvent], None] | None
    cancelled: Callable[[], bool] | None


_channel: _Channel | None = None


def _init_worker(log_queue: Any, level: int, events: Any, cancel: Any) -> None:
    global _channel
    # Ctrl+C reaches every process attached to the console. Workers ignore it so
    # the main process alone handles it: it asks them to stop and cleans up.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _channel = _Channel(events.put, cancel.is_set)
    if log_queue is None:
        return
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(QueueHandler(log_queue))
    root.setLevel(level)


def _call(fn: Callable[..., Any], item: Path, *args: Any) -> BatchResult:
    started = time.perf_counter()
    channel = _channel or _Channel(None, None)
    try:
        with progress.reporting(item, channel.emit, channel.cancelled):
            progress.check_cancelled()
            value = fn(item, *args)
    except Cancelled:
        logger.info("Stopped %s", item)
        return BatchResult(
            item=item, ok=False, cancelled=True, seconds=time.perf_counter() - started
        )
    except Exception as exc:  # noqa: BLE001 - one failing video must not abort the batch
        logger.debug("Details of the failure of %s", item, exc_info=True)
        return BatchResult(
            item=item,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
            seconds=time.perf_counter() - started,
            hint=hint_for(exc, offline=is_offline(item)),
        )
    return BatchResult(item=item, ok=True, value=value, seconds=time.perf_counter() - started)
