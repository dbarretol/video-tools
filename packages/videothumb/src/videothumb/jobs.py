"""Process one video or a whole folder; the same code serves the CLI and the GUI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from videocore import (
    BatchResult,
    ItemResult,
    OutputSlot,
    RunOptions,
    Status,
    part_path,
    remove_partials,
    run_task,
)
from videocore.verify import FrameCount, OutputError, check_image
from videothumb.common import Mode
from videothumb.sheet import SheetOptions, default_sheet_path, make_sheet
from videothumb.snapshots import (
    FOLDER_SUFFIX,
    SnapshotOptions,
    default_snapshot_dir,
    make_snapshots,
)

Options = SheetOptions | SnapshotOptions

# Rough seconds per frame with keyframe sampling, measured on 1080p H.264; precise
# sampling depends too much on the keyframe interval to estimate.
SECONDS_PER_SHEET_FRAME = 0.05
SECONDS_PER_SNAPSHOT = 0.1


@dataclass
class Produced:
    """What a task run returns: its output and how many frames it could decode."""

    output: Path
    frames: FrameCount = field(default_factory=FrameCount)
    files: list[Path] = field(default_factory=list)
    """The snapshots written."""


@dataclass(frozen=True)
class SheetTask:
    """``videocore.jobs.Task`` that makes one contact sheet per video."""

    name: str = "sheet"

    def output(self, slot: OutputSlot) -> Path:
        return slot_output(slot, Mode.SHEET)

    def state(self, output: Path, name: str, options: SheetOptions) -> Status | None:
        return Status.SKIPPED if has_output(output, Mode.SHEET) else None

    def run(
        self, video: Path, output: Path, name: str, options: SheetOptions, decoders: int | None
    ) -> Produced:
        produced = Produced(output)
        make_sheet(video, output, options, decoders=decoders, counts=produced.frames)
        return produced

    def verify(self, output: Path, value: Produced, options: SheetOptions) -> list[str]:
        width, _ = check_image(output)
        expected = options.columns * options.thumbnail_width
        if width != expected:
            raise OutputError(f"{output.name} is {width} pixels wide, expected {expected}")
        problem = value.frames.problem("contact sheet")
        return [problem] if problem else []

    def cleanup(self, output: Path) -> None:
        part_path(output).unlink(missing_ok=True)

    def estimate(self, duration: float, options: SheetOptions) -> float | None:
        if options.sampling == "precise":
            return None
        return 0.3 + options.count * SECONDS_PER_SHEET_FRAME


@dataclass(frozen=True)
class SnapshotsTask:
    """``videocore.jobs.Task`` that saves a folder of frames per video."""

    name: str = "frames"

    def output(self, slot: OutputSlot) -> Path:
        return slot_output(slot, Mode.FRAMES)

    def state(self, output: Path, name: str, options: SnapshotOptions) -> Status | None:
        return Status.SKIPPED if has_output(output, Mode.FRAMES) else None

    def run(
        self, video: Path, output: Path, name: str, options: SnapshotOptions, decoders: int | None
    ) -> Produced:
        produced = Produced(output)
        produced.files = make_snapshots(
            video, output, options, decoders=decoders, counts=produced.frames, name=name
        )
        return produced

    def verify(self, output: Path, value: Produced, options: SnapshotOptions) -> list[str]:
        for path in value.files:
            check_image(path)
        problem = value.frames.problem("frames")
        return [problem] if problem else []

    def cleanup(self, output: Path) -> None:
        remove_partials(output)

    def estimate(self, duration: float, options: SnapshotOptions) -> float | None:
        if options.sampling == "precise":
            return None
        return 0.3 + options.count * SECONDS_PER_SNAPSHOT


TASKS: dict[Mode, SheetTask | SnapshotsTask] = {
    Mode.SHEET: SheetTask(),
    Mode.FRAMES: SnapshotsTask(),
}


def default_output(video: Path, mode: Mode) -> Path:
    """Sheet file or snapshot folder next to the video."""
    return default_sheet_path(video) if mode is Mode.SHEET else default_snapshot_dir(video)


def batch_output(video: Path, mode: Mode, root: Path | None) -> Path:
    """Output of `video` in a batch: inside `root` when given, otherwise next to the video."""
    return slot_output(OutputSlot(root or video.parent, video.stem), mode)


def slot_output(slot: OutputSlot, mode: Mode) -> Path:
    """Sheet file or frames folder of a planned slot (see ``videocore.outputs``)."""
    if mode is Mode.SHEET:
        return slot.folder / f"{slot.name}_sheet.jpg"
    return slot.folder / f"{slot.name}{FOLDER_SUFFIX}"


def process(video: Path, mode: Mode, options: Options, output: Path | None = None) -> Path:
    """Process one video; return the sheet file or the snapshot folder."""
    output = output or default_output(video, mode)
    if mode is Mode.SHEET:
        assert isinstance(options, SheetOptions)
        return make_sheet(video, output, options)
    assert isinstance(options, SnapshotOptions)
    make_snapshots(video, output, options)
    return output


def process_folder(
    videos: list[Path],
    mode: Mode,
    options: Options,
    *,
    root: Path | None = None,
    source: Path | None = None,
    skip_existing: bool = False,
    workers: int | None = None,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Process `videos` in parallel processes; one failure does not stop the others.

    Kept for the tkinter app; new code uses ``videocore.jobs`` with `TASKS`.
    Outputs go next to each video, or inside `root` (recreating the subfolders
    of `source`). With `skip_existing`, videos whose output already exists are
    returned with ``skipped=True``. Videos not finished when cancelled are left
    out of the results.
    """
    run = RunOptions(output=root, workers=workers, overwrite=not skip_existing)

    def progress(done: int, total: int, result: ItemResult) -> None:
        if on_progress is not None:
            on_progress(done, total, _batch_result(result))

    report = run_task(
        TASKS[mode],
        videos,
        options,
        run,
        source=source,
        on_progress=progress,
        should_cancel=should_cancel,
    )
    return [_batch_result(item) for item in report.items if item.status is not Status.CANCELLED]


def _batch_result(item: ItemResult) -> BatchResult:
    skipped = item.status in (Status.SKIPPED, Status.OUTDATED)
    return BatchResult(
        item=item.video,
        ok=item.ok,
        value=item.output,
        error=item.error,
        skipped=skipped,
        error_type=item.error_type,
        seconds=item.seconds,
    )


def has_output(output: Path, mode: Mode) -> bool:
    """A sheet file, or a frames folder holding at least one frame.

    Files are written under a temporary name and renamed when finished, so an
    existing file is never a truncated one.
    """
    if mode is Mode.SHEET:
        return output.is_file()
    return output.is_dir() and any(output.glob("*_f[0-9][0-9][0-9]*.jpg"))
