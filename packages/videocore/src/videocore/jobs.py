"""Plan and run a tool's task over many videos: the contract every CLI and GUI shares.

A tool describes its work as a `Task`: where the output of a video goes, whether
an existing output is still good, how to process one video and how to clean up
after an interrupted one. Running it has two steps:

1. `plan` resolves every output, detects collisions, finds the videos that are
   already done (or done with other settings) and measures the video to process.
   Nothing is written, so it also serves ``--dry-run`` and previews.
2. `execute` processes the pending videos in parallel and returns a `RunReport`
   with one `ItemResult` per planned video, in plan order. Each video is checked
   with `Task.verify` right after being processed: OK, DEGRADED (incomplete
   outputs) or FAILED.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from videocore.batch import BatchResult, default_workers, is_offline, run_parallel
from videocore.errors import EXIT_DEGRADED, EXIT_FAILED, EXIT_INTERRUPTED, EXIT_OK
from videocore.inputs import InvalidInput
from videocore.logging_setup import default_log_dir
from videocore.options import option, validate
from videocore.outputs import OutputSlot, plan_outputs
from videocore.probe import format_hms, probe
from videocore.progress import ProgressEvent

logger = logging.getLogger(__name__)

PROBE_THREADS = 8
INVALID_HINT = "check the path; if it is in an input list, fix or remove that line"
KEPT_RUN_LISTS = 20
"""Runs whose retry lists are kept in the runs folder; older ones are deleted."""
PARALLEL_GAIN = 0.7
"""N videos in parallel finish about N**PARALLEL_GAIN times faster than one after another."""


class Status(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    """Processed, but some outputs are incomplete (see `ItemResult.problems`)."""
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

    def verify(self, output: Path, value: Any, options: T) -> list[str]:
        """Check the outputs just written; runs in the worker right after `run`.

        `value` is what `run` returned. Raise (e.g. ``videocore.verify.OutputError``)
        when an output is missing or unusable; return the problems of outputs that
        are only incomplete, which make the video DEGRADED. See ``videocore.verify``.
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
    """None when it will be processed; SKIPPED or OUTDATED when already done; FAILED
    when it cannot be processed (see `error`)."""
    offline: bool = False
    """Only in the cloud (e.g. a OneDrive placeholder): reading it downloads it."""
    duration: float | None = None
    """Seconds of video, measured for pending items only; None when unknown."""
    error: str | None = None
    """Why a FAILED item cannot be processed, e.g. "not found"."""

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
        if invalid := self.count(Status.FAILED):
            parts.append(f"{invalid} not found")
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
            if item.state is Status.FAILED:
                lines.append(f"  [skip, {item.error}] {self.show(item.video)}")
                continue
            action = "process" if item.pending else f"skip, {labels.get(item.state, item.state)}"
            lines.append(f"  [{action}] {self.show(item.video)} -> {self.show(item.output)}")
        return lines

    def show(self, path: Path) -> str:
        """`path` relative to the scanned folder or the output folder, when inside them."""
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
    source: Path | Sequence[Path | None] | None = None,
    outputs: Sequence[Path] | None = None,
    invalid: Sequence[InvalidInput] = (),
    measure: bool = True,
    speed: float = 1.0,
) -> Plan[T]:
    """Decide what to do with each video; nothing is written.

    Outputs follow ``videocore.outputs.plan_outputs`` (next to each video or
    inside ``run.output``, recreating the subfolders of `source`, which can
    also be one folder per video, as in ``videocore.Inputs.roots``), unless
    `outputs` gives one explicit output per video. Raises
    ``videocore.OutputCollisionError`` if two videos would write the same output.
    Unless ``run.overwrite``, videos whose outputs are already finished are
    marked SKIPPED or OUTDATED (see `Task.state`). With `measure`, the duration
    of each pending video is read, except for files only available in the cloud.
    `speed` corrects the task's estimates for this computer (see `Plan.speed`).
    `invalid` inputs (e.g. listed paths that do not exist) are planned as FAILED,
    after the videos, so the report lists them.
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
    for entry in invalid:
        failed = PlannedItem(entry.path, entry.path, entry.path.name, Status.FAILED)
        items.append(dataclasses.replace(failed, error=entry.reason))
    if isinstance(source, list | tuple):
        roots = set(source)
        source = roots.pop() if len(roots) == 1 else None
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
    problems: list[str] = field(default_factory=list)
    """Why a DEGRADED video's outputs are incomplete."""
    hint: str | None = None
    """What the user can do about a failure, e.g. "the disk is full; ..."."""

    @property
    def ok(self) -> bool:
        """Its outputs exist: OK, DEGRADED, SKIPPED or OUTDATED."""
        return self.status in (Status.OK, Status.DEGRADED, *DONE_STATES)


@dataclass
class RunReport:
    plan: Plan[Any]
    items: list[ItemResult] = field(default_factory=list)
    """One result per planned video, in plan order."""
    interrupted: bool = False
    """Stopped with Ctrl+C; the unfinished videos are CANCELLED."""

    def count(self, status: Status) -> int:
        return sum(item.status is status for item in self.items)

    @property
    def failed(self) -> list[ItemResult]:
        return [item for item in self.items if item.status is Status.FAILED]

    @property
    def cancelled(self) -> bool:
        return any(item.status is Status.CANCELLED for item in self.items)

    @property
    def degraded(self) -> list[ItemResult]:
        return [item for item in self.items if item.status is Status.DEGRADED]

    @property
    def exit_code(self) -> int:
        return self.exit_status()

    @property
    def unfinished(self) -> list[ItemResult]:
        return [item for item in self.items if item.status is Status.CANCELLED]

    def exit_status(self, *, strict: bool = False) -> int:
        """130 if interrupted, 1 if a video failed, 3 if some are DEGRADED (1 with `strict`),
        else 0."""
        if self.interrupted:
            return EXIT_INTERRUPTED
        if self.failed:
            return EXIT_FAILED
        if self.degraded:
            return EXIT_FAILED if strict else EXIT_DEGRADED
        return EXIT_OK

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
            done = result.status in (Status.OK, Status.DEGRADED)
            if not done or result.seconds is None or not item.duration:
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
    as CANCELLED. Ctrl+C does the same and returns the report with
    ``interrupted=True`` instead of raising, so the caller can say what was
    finished. Unfinished files are removed in every case.
    """
    pending = planned.pending
    # Videos already run in parallel; one decoder per video avoids oversubscription.
    decoders = 1 if planned.workers > 1 else None
    fn = functools.partial(_run_item, task=planned.task, options=planned.options, decoders=decoders)
    outputs = {item.video: item.output for item in pending}
    reported: list[BatchResult] = []
    interrupted = False

    def progress(done: int, total: int, result: BatchResult) -> None:
        reported.append(result)
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
    except KeyboardInterrupt:
        interrupted, finished = True, reported
        logger.info("Interrupted after %d of %d videos", len(reported), len(pending))
    finally:
        # Workers killed on cancel or Ctrl+C leave their unfinished files behind.
        for item in pending:
            planned.task.cleanup(item.output)

    by_video = {result.item: result for result in finished}
    report = RunReport(planned, interrupted=interrupted)
    for item in planned.items:
        if item.state is not None:
            hint = INVALID_HINT if item.state is Status.FAILED else None
            report.items.append(
                ItemResult(item.video, item.output, item.state, error=item.error, hint=hint)
            )
        elif item.video in by_video:
            report.items.append(_item_result(by_video[item.video], item.output))
        else:
            report.items.append(ItemResult(item.video, item.output, Status.CANCELLED))
    return report


@dataclass(frozen=True)
class RunLists:
    """Files listing the videos of a run to retry, one path per line."""

    failed: Path | None = None
    """Videos that failed or whose outputs are incomplete."""
    pending: Path | None = None
    """Videos not finished because the run was cancelled or interrupted."""


def default_runs_dir(app: str) -> Path:
    """%LOCALAPPDATA%/<app>/runs on Windows, $XDG_STATE_HOME/<app>/runs elsewhere."""
    return default_log_dir(app).parent / "runs"


def write_run_lists(
    report: RunReport, folder: Path, *, title: str, now: datetime | None = None
) -> RunLists:
    """Write ``<time>_<task>_failed.txt`` and ``..._pending.txt`` into `folder` when needed.

    Each file has one absolute path per line and ``#`` comments, in UTF-8 with a
    BOM so that PowerShell 5.1 reads it correctly. `title` describes the run in
    the first line (e.g. the command). Only the lists of the newest
    `KEPT_RUN_LISTS` runs are kept. Errors writing them are logged and ignored.
    """
    now = now or datetime.now()
    stamp = f"{now:%Y%m%d-%H%M%S}_{report.plan.task.name}"
    header = [f"# {title}", f"# {now:%Y-%m-%d %H:%M}"]
    failed = [*report.failed, *report.degraded]
    lists = {}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if failed:
            lines = [*header, "# Videos that failed or whose outputs are incomplete:"]
            for item in failed:
                reason = item.error if item.status is Status.FAILED else "; ".join(item.problems)
                lines += [f"# {item.status.upper()}: {reason}", str(item.video.resolve())]
            lists["failed"] = _write_list(folder / f"{stamp}_failed.txt", lines)
        if unfinished := report.unfinished:
            lines = [*header, "# Videos not finished:"]
            lines += [str(item.video.resolve()) for item in unfinished]
            lists["pending"] = _write_list(folder / f"{stamp}_pending.txt", lines)
        _prune_run_lists(folder)
    except OSError:
        logger.warning("Could not write the lists of videos to retry in %s", folder, exc_info=True)
    return RunLists(**lists)


def _write_list(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def _prune_run_lists(folder: Path) -> None:
    lists = sorted(folder.glob("*_failed.txt")) + sorted(folder.glob("*_pending.txt"))
    runs = sorted({path.name.split("_", 1)[0] for path in lists}, reverse=True)
    old = set(runs[KEPT_RUN_LISTS:])
    for path in lists:
        if path.name.split("_", 1)[0] in old:
            path.unlink(missing_ok=True)


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
    value = task.run(video, output, name, options, decoders)
    problems = list(task.verify(output, value, options))
    for problem in problems:
        logger.warning("%s: %s", video.name, problem)
    return _Outcome(value, problems)


@dataclass(frozen=True)
class _Outcome:
    value: Any
    problems: list[str]


def _item_result(result: BatchResult, output: Path) -> ItemResult:
    outcome = result.value if isinstance(result.value, _Outcome) else _Outcome(result.value, [])
    if result.cancelled:
        status = Status.CANCELLED
    elif not result.ok:
        status = Status.FAILED
    else:
        status = Status.DEGRADED if outcome.problems else Status.OK
    return ItemResult(
        video=result.item,
        output=output,
        status=status,
        value=outcome.value,
        error=result.error,
        error_type=result.error_type,
        seconds=result.seconds,
        problems=outcome.problems,
        hint=result.hint,
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
