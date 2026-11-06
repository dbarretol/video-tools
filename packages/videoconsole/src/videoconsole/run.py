"""Show a planned run, its live progress and its final report.

`RunProgress` draws, while ``videocore.execute`` runs, one bar for the whole
batch and one row per video being processed with the stage it is in. When
stderr is not a terminal (redirected, in scripts and tests) it prints one line
per finished video instead.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from videoconsole.console import Console
from videocore import (
    ItemResult,
    Plan,
    ProgressEvent,
    RunLists,
    RunReport,
    Status,
    default_runs_dir,
    write_run_lists,
)

logger = logging.getLogger(__name__)

RESULT_STYLES = {
    Status.OK: "green",
    Status.DEGRADED: "yellow",
    Status.FAILED: "red",
    Status.CANCELLED: "yellow",
}


@dataclass(frozen=True)
class Wording:
    """How a tool names what it does, e.g. "archived" instead of "succeeded"."""

    verb: str = "Processing"
    """Label of the progress bar of the batch."""
    done: str = "succeeded"
    degraded: str = "incomplete"
    skipped: str = "skipped (output already exists)"
    outdated: str = "done with other settings (not redone)"
    redo_hint: str = "use --overwrite to redo them"
    """Shown in the plan when some videos are already done."""
    stages: Mapping[str, str] = field(default_factory=dict)
    """Readable names of the stages a task reports, e.g. ``{"sha256": "file hash"}``."""


def plan_document(planned: Plan[Any]) -> dict[str, Any]:
    """The plan as JSON data: what will happen to each video, and the estimates."""
    counts = {"pending": len(planned.pending)}
    counts |= {str(state): planned.count(state) for state in (Status.SKIPPED, Status.OUTDATED)}
    return {
        "task": planned.task.name,
        "source": planned.source,
        "output": planned.run.output,
        "videos": len(planned.items),
        "counts": counts,
        "workers": planned.workers,
        "duration_seconds": planned.duration,
        "eta_seconds": planned.eta(),
        "items": [
            {
                "video": item.video,
                "output": item.output,
                "state": str(item.state) if item.state is not None else "pending",
                "duration_seconds": item.duration,
                "offline": item.offline,
            }
            for item in planned.items
        ],
    }


def report_document(report: RunReport) -> dict[str, Any]:
    """The results as JSON data: one entry per planned video, in plan order."""
    return {
        "interrupted": report.interrupted,
        "counts": {str(status): report.count(status) for status in Status},
        "items": [
            {
                "video": item.video,
                "output": item.output,
                "status": str(item.status),
                "seconds": item.seconds,
                "error": item.error,
                "error_type": item.error_type,
                "hint": item.hint,
                "problems": item.problems,
            }
            for item in report.items
        ],
    }


def record_report(console: Console, report: RunReport) -> None:
    """Add the results to the ``--json`` document."""
    console.record(report=report_document(report))


def print_plan(
    console: Console, planned: Plan[Any], wording: Wording, *, items: bool = False
) -> None:
    """The plan summary (number of videos, estimated time, output); with `items`, every video."""
    console.record(plan=plan_document(planned))
    for line in planned.summary(redo_hint=wording.redo_hint):
        console.info(line)
    if items:
        for line in planned.item_lines():
            console.info(line)


class RunProgress:
    """``on_progress`` and ``on_event`` callbacks for ``videocore.execute`` that show progress.

    Use it as a context manager around ``execute``.
    """

    def __init__(self, console: Console, planned: Plan[Any], wording: Wording) -> None:
        self.console = console
        self.planned = planned
        self.wording = wording
        console.record(plan=plan_document(planned))
        pending = planned.pending
        known = [item.duration for item in pending if item.duration]
        typical = sum(known) / len(known) if known else 1.0
        # The batch bar advances by seconds of video when durations are known, so its
        # time remaining accounts for long and short videos.
        self._weights = {item.video: item.duration or typical for item in pending}
        self._rows: dict[Path, TaskID] = {}
        self._stages: set[tuple[Path, str]] = set()
        self._batch: TaskID | None = None
        self._progress: Progress | None = None
        if console.live:
            self._progress = Progress(
                TextColumn("{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console.err,
                transient=True,
                redirect_stdout=False,
                redirect_stderr=False,
            )

    def __enter__(self) -> RunProgress:
        if self._progress is not None:
            self._progress.start()
            if len(self._weights) > 1:
                self._batch = self._progress.add_task(
                    self._batch_label(0), total=sum(self._weights.values())
                )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._progress is not None:
            self._progress.stop()

    def _batch_label(self, done: int) -> str:
        return f"{self.wording.verb} {done}/{len(self._weights)} videos"

    def on_event(self, event: ProgressEvent) -> None:
        item = Path(event.item)
        if event.kind == "stage" and event.stage and (item, event.stage) not in self._stages:
            self._stages.add((item, event.stage))
            logger.debug("%s: %s", item.name, self._stage_label(event.stage))
        if self._progress is None:
            return
        name = self._name(item)
        if event.kind == "started":
            self._rows[item] = self._progress.add_task(f"  {name}", total=None)
            return
        row = self._rows.get(item)
        if row is None or event.stage is None:
            return
        self._progress.update(
            row,
            description=f"  {name}: {self._stage_label(event.stage)}",
            total=event.total or None,
            completed=event.done,
        )

    def _name(self, video: Path) -> str:
        """Relative to the folder of a batch; just the file name for a single video."""
        return self.planned.show(video) if self.planned.source is not None else video.name

    def _stage_label(self, stage: str) -> str:
        return self.wording.stages.get(stage, stage)

    def on_progress(self, done: int, total: int, result: ItemResult) -> None:
        status = result.status.upper()
        line = Text(f"[{done}/{total}] {self._name(result.video)}: ")
        line.append(status, style=RESULT_STYLES.get(result.status))
        if self._progress is None:
            if not self.console.quiet:
                self.console.out.print(line)
            return
        if (row := self._rows.pop(result.video, None)) is not None:
            self._progress.remove_task(row)
        if self._batch is not None:
            weight = self._weights.get(result.video, 0.0)
            self._progress.update(self._batch, advance=weight, description=self._batch_label(done))
        self._progress.console.print(line)


def print_report(
    console: Console, report: RunReport, wording: Wording, *, log_file: Path | None
) -> None:
    """The counts of the run, then what went wrong with each video and how to fix it."""
    record_report(console, report)
    parts = [f"{report.count(Status.OK)} {wording.done}"]
    if degraded := report.degraded:
        parts.append(f"{len(degraded)} {wording.degraded}")
    if skipped := report.count(Status.SKIPPED):
        parts.append(f"{skipped} {wording.skipped}")
    if outdated := report.count(Status.OUTDATED):
        parts.append(f"{outdated} {wording.outdated}")
    failed = report.failed
    parts.append(f"{len(failed)} failed")
    if unfinished := report.unfinished:
        parts.append(f"{len(unfinished)} not finished")
    style = "error" if failed else "warning" if degraded or unfinished else "ok"
    console.info("Done: " + ", ".join(parts) + ".", style)

    show = report.plan.show
    for result in degraded:
        problems = "; ".join(result.problems)
        console.detail(f"  - {show(result.video)}: {wording.degraded}: {problems}")
    for result in failed:
        console.detail(f"  - {show(result.video)}: {result.error}", "error")
        if result.hint:
            console.detail(f"    Hint: {result.hint}")
    if failed:
        console.detail(f"Details: {log_file}", "dim")
    if report.interrupted:
        console.detail("Interrupted; unfinished files were removed.", "warning")


def report_run_lists(
    console: Console, report: RunReport, app: str, *, title: str, resume: str
) -> RunLists:
    """Write the lists of videos to retry or resume, and say where they are and what to do.

    `resume` tells how to finish an interrupted run, e.g. "run the same command
    again". Shown even when quiet, since it needs action.
    """
    lists = write_run_lists(report, default_runs_dir(app), title=title)
    console.record(run_lists={"failed": lists.failed, "pending": lists.pending})
    if lists.failed:
        console.notice(f"Videos to retry are listed in {lists.failed}")
    if lists.pending:
        console.notice(f"Unfinished videos are listed in {lists.pending}; to resume, {resume}")
    return lists
