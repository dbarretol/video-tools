"""Progress inside a video and cooperative cancellation.

Task code reports where it is with `stage` and calls `check_cancelled` in its
loops; both are no-ops outside a run, so the same functions work from scripts
and tests. The runner (``videocore.batch.run_parallel``) sets up the context
for each item: events from worker processes travel back over a queue, and a
cancel request reaches them through a shared event, so a video stops at the
next frame and cleans up after itself instead of being killed.

The context is process-wide rather than per thread, because tasks decode in
helper threads too. A process runs one item at a time.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

STAGE_INTERVAL_SECONDS = 0.2
"""Minimum time between two events of the same stage, so loops can report every step."""


class Cancelled(BaseException):
    """The run was cancelled. Raised by `check_cancelled` inside a task.

    Like KeyboardInterrupt it is not an ``Exception``, so ``except Exception``
    blocks in tasks do not mistake it for a failure.
    """


@dataclass(frozen=True)
class ProgressEvent:
    item: Path
    kind: Literal["started", "stage"]
    stage: str | None = None
    done: int = 0
    total: int = 0
    """0 when the stage has no measurable size."""

    @property
    def fraction(self) -> float | None:
        return self.done / self.total if self.total else None


class CancelToken:
    """A cancel request that can be passed wherever a ``should_cancel`` callable is expected."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __call__(self) -> bool:
        return self._event.is_set()


class _Context:
    def __init__(
        self, item: Path, emit: Callable[[ProgressEvent], None], cancelled: Callable[[], bool]
    ) -> None:
        self.item = item
        self.emit = emit
        self.cancelled = cancelled
        self.lock = threading.Lock()
        self.last: dict[str, float] = {}


_current: _Context | None = None


@contextmanager
def reporting(
    item: Path,
    emit: Callable[[ProgressEvent], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Make `stage` and `check_cancelled` report on `item` until the block ends."""
    global _current
    previous = _current
    _current = _Context(item, emit or (lambda event: None), cancelled or (lambda: False))
    try:
        _current.emit(ProgressEvent(item, "started"))
        yield
    finally:
        _current = previous


def stage(name: str, done: int = 0, total: int = 0) -> None:
    """Report progress of stage `name` of the current item, e.g. ``stage("snapshots", 3, 150)``.

    Events of the same stage are throttled, except its first and last steps.
    Safe to call from helper threads.
    """
    context = _current
    if context is None:
        return
    now = time.monotonic()
    with context.lock:
        last = context.last.get(name)
        finished = total > 0 and done >= total
        if last is not None and not finished and now - last < STAGE_INTERVAL_SECONDS:
            return
        context.last[name] = now
    context.emit(ProgressEvent(context.item, "stage", name, done, total))


def check_cancelled() -> None:
    """Raise `Cancelled` if the current run was asked to stop."""
    context = _current
    if context is not None and context.cancelled():
        raise Cancelled(f"Cancelled while processing {context.item}")
