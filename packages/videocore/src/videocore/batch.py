"""Batch processing of many videos with per-item error isolation."""

from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".mpg", ".mpeg"}
)


@dataclass
class BatchResult:
    item: Path
    ok: bool
    value: Any = None
    error: str | None = None


def find_videos(
    folder: str | os.PathLike[str],
    *,
    recursive: bool = False,
    extensions: Iterable[str] = VIDEO_EXTENSIONS,
) -> list[Path]:
    """List video files in `folder`, sorted by path."""
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"Folder not found: {root}")
    suffixes = {ext.lower() for ext in extensions}
    candidates = root.rglob("*") if recursive else root.iterdir()
    return sorted(p for p in candidates if p.is_file() and p.suffix.lower() in suffixes)


def default_workers() -> int:
    """Roughly one worker per physical core; decoders also use threads internally."""
    return max(1, (os.cpu_count() or 2) // 2)


def run_parallel(
    fn: Callable[[Path], Any],
    items: Sequence[Path],
    *,
    workers: int | None = None,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Apply `fn` to every item, isolating failures, and return results in input order.

    With more than one worker `fn` runs in separate processes, so it must be a
    module-level (picklable) function. ``workers=1`` runs inline, which is easier
    to debug. `on_progress(done, total, result)` is called as each item finishes;
    when `should_cancel()` returns True, pending items are not started and are
    left out of the results.

    Log records emitted in worker processes are forwarded to the handlers of
    this process's root logger, so they end up in the same log file and console.
    """
    workers = workers or default_workers()
    total = len(items)
    results: dict[int, BatchResult] = {}

    def record(index: int, result: BatchResult) -> None:
        results[index] = result
        if not result.ok:
            logger.warning("Failed to process %s: %s", result.item, result.error)
        if on_progress is not None:
            on_progress(len(results), total, result)

    if workers == 1 or total <= 1:
        for index, item in enumerate(items):
            if should_cancel is not None and should_cancel():
                break
            record(index, _call(fn, item))
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
            pool.submit(_call, fn, item): index for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            record(futures[future], future.result())
            if should_cancel is not None and should_cancel():
                for pending in futures:
                    pending.cancel()
                break
    return [results[i] for i in sorted(results)]


@contextmanager
def _forward_worker_logs(
    context: multiprocessing.context.SpawnContext,
) -> Iterator[tuple[Callable[..., None] | None, tuple[Any, ...]]]:
    """Yield a pool initializer that sends worker log records to this process's handlers."""
    root = logging.getLogger()
    if not root.handlers:
        yield None, ()
        return
    log_queue = context.Queue()
    listener = QueueListener(log_queue, *root.handlers, respect_handler_level=True)
    listener.start()
    try:
        yield _init_worker_logging, (log_queue, root.level)
    finally:
        listener.stop()
        log_queue.close()


def _init_worker_logging(log_queue: Any, level: int) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(QueueHandler(log_queue))
    root.setLevel(level)


def _call(fn: Callable[[Path], Any], item: Path) -> BatchResult:
    try:
        return BatchResult(item=item, ok=True, value=fn(item))
    except Exception as exc:  # noqa: BLE001 - one failing video must not abort the batch
        return BatchResult(item=item, ok=False, error=f"{type(exc).__name__}: {exc}")
