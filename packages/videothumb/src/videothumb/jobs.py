"""Process one video or a whole folder; the same code serves the CLI and the GUI."""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import Callable
from pathlib import Path

from videocore import BatchResult, default_workers, run_parallel
from videothumb.common import Mode
from videothumb.sheet import SheetOptions, default_sheet_path, make_sheet
from videothumb.snapshots import SnapshotOptions, default_snapshot_dir, make_snapshots

Options = SheetOptions | SnapshotOptions


def default_output(video: Path, mode: Mode) -> Path:
    """Sheet file or snapshot folder next to the video."""
    return default_sheet_path(video) if mode is Mode.SHEET else default_snapshot_dir(video)


def batch_output(video: Path, mode: Mode, root: Path | None) -> Path:
    """Output of `video` in a batch: inside `root` when given, otherwise next to the video."""
    default = default_output(video, mode)
    return default if root is None else root / default.name


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
    workers: int | None = None,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Process `videos` in parallel processes; one failure does not stop the others."""
    workers = workers or default_workers()
    if workers > 1:
        # Videos already run in parallel; one decoder per video avoids oversubscription.
        options = _with_workers(options, 1)
    task = functools.partial(_process_in_batch, mode=mode, options=options, root=root)
    return run_parallel(
        task, videos, workers=workers, on_progress=on_progress, should_cancel=should_cancel
    )


def _process_in_batch(video: Path, *, mode: Mode, options: Options, root: Path | None) -> Path:
    # Module-level so it can be pickled into worker processes.
    return process(video, mode, options, batch_output(video, mode, root))


def _with_workers(options: Options, workers: int) -> Options:
    return dataclasses.replace(options, workers=workers)
