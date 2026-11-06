"""Plan and run a tool's task over many videos: the contract every CLI and GUI shares.

A tool describes its work as a `Task`: where the output of a video goes, whether
an existing output is still good, how to process one video and how to clean up
after an interrupted one. Running it has two steps:

1. `plan` resolves every output, detects collisions, finds the videos that are
   already done (or done with other settings) and measures the video to process.
   Nothing is written, so it also serves ``--dry-run`` and previews.
2. `execute` processes the pending videos in parallel and returns a `RunReport`
   with one `ItemResult` per planned video, in plan order.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from videocore.batch import BatchResult, default_workers, is_offline, run_parallel
from videocore.errors import EXIT_FAILED, EXIT_OK
from videocore.options import option, validate
from videocore.outputs import OutputSlot, plan_outputs
from videocore.probe import format_hms, probe
from videocore.progress import ProgressEvent

logger = logging.getLogger(__name__)

PROBE_THREADS = 8
PARALLEL_GAIN = 0.7
"""N videos in parallel finish about N**PARALLEL_GAIN times faster than one after another."""


class Status(StrEnum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Its outputs already exist and match the options; not processed."""
    OUTDATED = "outdated"
    """Its outputs exist but were made with other options; not processed."""
    CANCELLED = "cancelled"
    """Not finished because the run was cancelled or interrupted."""


DONE_STATES = (Status.SKIPPED, Status.OUTDATED)


@dataclass(frozen=True)
class RunOptions:
    """How to run a task, as opposed to what it produces (the task's own options)."""

    output: Path | None = option(
        None,
        "output folder; with -r, subfolders are recreated inside it",
        flags=("-o", "--output"),
        default_text="next to each video",
    )
    recursive: bool = option(
        False, "include subfolders when processing a folder", flags=("-r", "--recursive")
    )
    workers: int | None = option(
        None,
        "videos processed in parallel for folders",
        flags=("-w", "--workers"),
        gt=0,
        default_text="automatic",
        advanced=True,
    )
    overwrite: bool = False
    """Process videos again even when their outputs exist."""

    def __post_init__(self) -> None:
        validate(self)


class Task[T](Protocol):
    """What a tool does to one video. Instances are sent to worker processes."""

    name: str

    def output(self, slot: OutputSlot) -> Path:
        """The file or folder a planned slot produces (see ``videocore.outputs``)."""
        ...

    def state(self, output: Path, name: str, options: T) -> Status | None:
        """SKIPPED or OUTDATED when `output` already holds a finished result; else None."""
        ...

    def run(self, video: Path, output: Path, name: str, options: T, decoders: int | None) -> Any:
        """Process `video` into `output`; raise on failure. Runs in a worker process.

        `decoders` is how many containers may decode the video in parallel; None
        lets the task decide (one video at a time can use several).
        """
        ...

    def cleanup(self, output: Path) -> None:
        """Remove the unfinished files a killed run leaves in `output`."""
        ...

    def estimate(self, duration: float, options: T) -> float | None:
        """Rough seconds to process a video of `duration` seconds with one worker."""
        ...


@dataclass(frozen=True)
class PlannedItem:
    video: Path
    output: Path
    name: str
    """Base name of the outputs: the stem, or the file name when stems collide."""
    state: Status | None
    """None when it will be processed; SKIPPED or OUTDATED otherwise."""
    offline: bool = False
    """Only in the cloud (e.g. a OneDrive placeholder): reading it downloads it."""
    duration: float | None = None
    """Seconds of video, measured for pending items only; None when unknown."""

    @property
    def pending(self) -> bool:
        return self.state is None


@dataclass(frozen=True)
class Plan[T]:
    task: Task[T]
    options: T
    run: RunOptions
    items: tuple[PlannedItem, ...]
    source: Path | None = None
    """The folder that was scanned; paths are shown relative to it."""
    speed: float = 1.0
    """How many times longer than `Task.estimate` the task takes on this computer,
    learned from earlier runs (see ``videocore.state.State.speed``)."""

    @property
    def pending(self) -> list[PlannedItem]:
        return [item for item in self.items if item.pending]

    def count(self, state: Status | None) -> int:
        return sum(item.state == state for item in self.items)

    @property
    def workers(self) -> int:
        return min(self.run.workers or default_workers(), max(1, len(self.pending)))

    @property
    def duration(self) -> float:
        """Seconds of video to process (items whose duration is known)."""
        return sum(item.duration or 0.0 for item in self.pending)

    def eta(self) -> float | None:
        """Rough seconds the pending items of known duration will take.

        None when the task cannot estimate or no pending duration is known.
        """
        estimates = []
        for item in self.pending:
            if item.duration is None:
                continue
            estimate = self.task.estimate(item.duration, self.options)
            if estimate is None:
                return None
            estimates.append(estimate * self.speed)
        if not estimates:
            return None
        # Parallel workers share the CPU, so they do not divide the time evenly.
        return max(max(estimates), sum(estimates) / (self.workers**PARALLEL_GAIN))

    def summary(self, *, redo_hint: str = "use --overwrite to redo them") -> list[str]:
        """Lines describing the plan, shown before running it."""
        pending = self.pending
        parts = [f"{len(pending)} to process"]
        if skipped := self.count(Status.SKIPPED):
            parts.append(f"{skipped} already done")
        if outdated := self.count(Status.OUTDATED):
            parts.append(f"{outdated} done with other settings ({redo_hint})")
        noun = "video" if len(self.items) == 1 else "videos"
        lines = [f"{len(self.items)} {noun}: " + ", ".join(parts)]
        if pending:
            unknown = sum(item.duration is None for item in pending)
            if unknown == len(pending):
                line = "Video to process: unknown length"
            else:
                line = f"Video to process: {format_hms(self.duration)}"
                if unknown:
                    line += f" plus {unknown} of unknown length"
            eta = self.eta()
            if eta is not None:
                line += f"; estimated time: {_rough(eta)} with {self.workers} in parallel"
            lines.append(line)
        if offline := sum(item.offline for item in pending):
            lines.append(
                f"{offline} files are only in the cloud (e.g. OneDrive) and will be "
                "downloaded while they are read"
            )
        lines.append(f"Output: {self.run.output or 'next to each video'}")
        return lines

    def item_lines(self) -> list[str]:
        """One line per video saying what will happen to it, for ``--dry-run``."""
        labels = {Status.SKIPPED: "already done", Status.OUTDATED: "done with other settings"}
        lines = []
        for item in self.items:
            action = "process" if item.pending else f"skip, {labels.get(item.state, item.state)}"
            lines.append(f"  [{action}] {self._show(item.video)} -> {self._show(item.output)}")
        return lines

    def _show(self, path: Path) -> str:
        if self.source is not None and path.is_relative_to(self.source):
            return str(path.relative_to(self.source))
        output = self.run.output
        if output is not None and path.is_relative_to(output):
            return str(output.name / path.relative_to(output))
        return str(path)


def plan[T](
    task: Task[T],
    videos: Sequence[Path],
    options: T,
    run: RunOptions | None = None,
    *,
    source: Path | None = None,
    outputs: Sequence[Path] | None = None,
    measure: bool = True,
    speed: float = 1.0,
) -> Plan[T]:
    """Decide what to do with each video; nothing is written.

    Outputs follow ``videocore.outputs.plan_outputs`` (next to each video or
    inside ``run.output``, recreating the subfolders of `source`), unless
    `outputs` gives one explicit output per video. Raises
    ``videocore.OutputCollisionError`` if two videos would write the same output.
    Unless ``run.overwrite``, videos whose outputs are already finished are
    marked SKIPPED or OUTDATED (see `Task.state`). With `measure`, the duration
    of each pending video is read, except for files only available in the cloud.
    `speed` corrects the task's estimates for this computer (see `Plan.speed`).
    """
    run = run or RunOptions()
    if outputs is not None:
        if len(outputs) != len(videos):
            raise ValueError("outputs must have one path per video")
        targets = [(Path(out), video.stem) for video, out in zip(videos, outputs, strict=True)]
    else:
        slots = plan_outputs(videos, output=run.output, source=source)
        targets = [(task.output(slot), slot.name) for slot in slots]

    items = []
    for video, (output, name) in zip(videos, targets, strict=True):
        state = None if run.overwrite else task.state(output, name, options)
        items.append(PlannedItem(video, output, name, state, offline=is_offline(video)))
    if measure:
        items = _measure(items)
    return Plan(task, options, run, tuple(items), source=source, speed=speed)


@dataclass
class ItemResult:
    video: Path
    output: Path
    status: Status
    value: Any = None
    """What the task returned, for successful items."""
    error: str | None = None
    error_type: str | None = None
    """Exception class name, e.g. ``"VideoOpenError"``."""
    seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in (Status.OK, *DONE_STATES)


@dataclass
class RunReport:
    plan: Plan[Any]
    items: list[ItemResult] = field(default_factory=list)
    """One result per planned video, in plan order."""

    def count(self, status: Status) -> int:
        return sum(item.status is status for item in self.items)

    @property
    def failed(self) -> list[ItemResult]:
        return [item for item in self.items if item.status is Status.FAILED]

    @property
    def cancelled(self) -> bool:
        return any(item.status is Status.CANCELLED for item in self.items)

    @property
    def exit_code(self) -> int:
        return EXIT_FAILED if self.failed else EXIT_OK

    def measured_speed(self) -> float | None:
        """How many times longer than `Task.estimate` the processed videos took.

        Only successful videos of known duration count; None when there are none.
        The time of videos run in parallel is converted to the time of one alone.
        """
        planned = {item.video: item for item in self.plan.items}
        slowdown = self.plan.workers ** (1 - PARALLEL_GAIN)
        took = expected = 0.0
        for result in self.items:
            item = planned[result.video]
            if result.status is not Status.OK or result.seconds is None or not item.duration:
                continue
            estimate = self.plan.task.estimate(item.duration, self.plan.options)
            if not estimate:
                continue
            took += result.seconds / slowdown
            expected += estimate
        return took / expected if expected else None


def execute[T](
    planned: Plan[T],
    *,
    on_progress: Callable[[int, int, ItemResult], None] | None = None,
    on_event: Callable[[ProgressEvent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RunReport:
    """Process the pending items of `planned` in parallel; one failure does not stop the others.

    `on_progress(done, total, result)` is called as each pending item finishes,
    and `on_event` receives the progress events from inside each video (see
    ``videocore.batch.run_parallel``). When `should_cancel()` returns True,
    running videos stop at their next step; every unfinished item is reported
    as CANCELLED. Unfinished files are removed even when this call is
    interrupted (Ctrl+C), which then propagates.
    """
    pending = planned.pending
    # Videos already run in parallel; one decoder per video avoids oversubscription.
    decoders = 1 if planned.workers > 1 else None
    fn = functools.partial(_run_item, task=planned.task, options=planned.options, decoders=decoders)
    outputs = {item.video: item.output for item in pending}

    def progress(done: int, total: int, result: BatchResult) -> None:
        if on_progress is not None:
            on_progress(done, total, _item_result(result, outputs[result.item]))

    try:
        finished = run_parallel(
            fn,
            [item.video for item in pending],
            item_args=[(item.output, item.name) for item in pending],
            workers=planned.workers,
            on_progress=progress,
            on_event=on_event,
            should_cancel=should_cancel,
        )
    finally:
        # Workers killed on cancel or Ctrl+C leave their unfinished files behind.
        for item in pending:
            planned.task.cleanup(item.output)

    by_video = {result.item: result for result in finished}
    report = RunReport(planned)
    for item in planned.items:
        if item.state is not None:
            report.items.append(ItemResult(item.video, item.output, item.state))
        elif item.video in by_video:
            report.items.append(_item_result(by_video[item.video], item.output))
        else:
            report.items.append(ItemResult(item.video, item.output, Status.CANCELLED))
    return report


def run_task[T](
    task: Task[T],
    videos: Sequence[Path],
    options: T,
    run: RunOptions | None = None,
    *,
    source: Path | None = None,
    on_progress: Callable[[int, int, ItemResult], None] | None = None,
    on_event: Callable[[ProgressEvent], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RunReport:
    """`plan` then `execute`, without measuring durations."""
    planned = plan(task, videos, options, run, source=source, measure=False)
    return execute(planned, on_progress=on_progress, on_event=on_event, should_cancel=should_cancel)


def _run_item(
    video: Path, target: tuple[Path, str], *, task: Task[Any], options: Any, decoders: int | None
) -> Any:
    # Module-level so it can be pickled into worker processes.
    output, name = target
    return task.run(video, output, name, options, decoders)


def _item_result(result: BatchResult, output: Path) -> ItemResult:
    return ItemResult(
        video=result.item,
        output=output,
        status=Status.CANCELLED
        if result.cancelled
        else (Status.OK if result.ok else Status.FAILED),
        value=result.value,
        error=result.error,
        error_type=result.error_type,
        seconds=result.seconds,
    )


def _measure(items: list[PlannedItem]) -> list[PlannedItem]:
    """Read the duration of pending, locally available items, a few at a time."""
    targets = [i for i, item in enumerate(items) if item.pending and not item.offline]
    if not targets:
        return items
    with ThreadPoolExecutor(max_workers=min(PROBE_THREADS, len(targets))) as pool:
        durations = list(pool.map(lambda i: _duration(items[i].video), targets))
    measured = list(items)
    for index, duration in zip(targets, durations, strict=True):
        measured[index] = dataclasses.replace(items[index], duration=duration)
    return measured


def _duration(video: Path) -> float | None:
    try:
        return probe(video).duration or None
    except Exception:  # noqa: BLE001 - unreadable files fail later, with a proper error
        logger.debug("Could not read the duration of %s", video, exc_info=True)
        return None


def _rough(seconds: float) -> str:
    """'about 40 s', 'about 12 min', 'about 2 h 10 min'."""
    if seconds < 60:
        return f"about {max(1, round(seconds))} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} min"
    return f"about {minutes // 60} h {minutes % 60:02d} min"
