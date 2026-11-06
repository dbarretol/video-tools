"""Process one video or a whole folder; the same code serves the CLI and the GUI."""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import Callable
from pathlib import Path

from videocore import (
    BatchResult,
    OutputSlot,
    default_workers,
    part_path,
    plan_outputs,
    remove_partials,
    run_parallel,
)
from videothumb.common import Mode
from videothumb.sheet import SheetOptions, default_sheet_path, make_sheet
from videothumb.snapshots import SnapshotOptions, default_snapshot_dir, make_snapshots

Options = SheetOptions | SnapshotOptions


def default_output(video: Path, mode: Mode) -> Path:
    """Sheet file or snapshot folder next to the video."""
    return default_sheet_path(video) if mode is Mode.SHEET else default_snapshot_dir(video)


def batch_output(video: Path, mode: Mode, root: Path | None) -> Path:
    """Output of `video` in a batch: inside `root` when given, otherwise next to the video."""
    return slot_output(OutputSlot(root or video.parent, video.stem), mode)


def slot_output(slot: OutputSlot, mode: Mode) -> Path:
    """Sheet file or snapshot folder of a planned slot (see ``videocore.outputs``)."""
    if mode is Mode.SHEET:
        return slot.folder / f"{slot.name}_sheet.jpg"
    # A renamed slot is the full file name (clip.mp4), which a folder next to the
    # video cannot take, so renamed snapshot folders get a suffix.
    return slot.folder / (f"{slot.name}_snapshots" if slot.renamed else slot.name)


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
    workers: int | None = None,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Process `videos` in parallel processes; one failure does not stop the others.

    Outputs go next to each video, or inside `root` when given; with `source`, the
    folder that was scanned, its subfolders are recreated inside `root`. Videos
    whose stems collide in the same place use their full file name instead
    (``clip.mp4_sheet.jpg``). Raises ``videocore.OutputCollisionError`` before
    processing anything if two videos would still write the same output.
    """
    slots = plan_outputs(videos, output=root, source=source)
    workers = workers or default_workers()
    if workers > 1:
        # Videos already run in parallel; one decoder per video avoids oversubscription.
        options = _with_workers(options, 1)
    task = functools.partial(_process_in_batch, mode=mode, options=options)
    try:
        return run_parallel(
            task,
            videos,
            item_args=slots,
            workers=workers,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
    finally:
        # Workers killed on cancel or Ctrl+C leave their unfinished files behind.
        for slot in slots:
            output = slot_output(slot, mode)
            if mode is Mode.SHEET:
                part_path(output).unlink(missing_ok=True)
            else:
                remove_partials(output)


def _process_in_batch(video: Path, slot: OutputSlot, *, mode: Mode, options: Options) -> Path:
    # Module-level so it can be pickled into worker processes.
    return process(video, mode, options, slot_output(slot, mode))


def _with_workers(options: Options, workers: int) -> Options:
    return dataclasses.replace(options, workers=workers)
