"""Process one video into its archive artifacts.

For ``movie.mp4`` the output folder gets:

- ``movie_info.txt``: metadata report.
- ``movie_summary.mp4``: info card followed by evenly spaced snapshots.
- ``movie_thumb.jpg``: grid of thumbnails.
- ``movie.fingerprint.json``: perceptual fingerprint.
- ``movie.archive.json``: manifest listing the above, written last; it says
  ``complete: true`` only when every requested artifact was written.

The video is decoded as few times as possible: the snapshots feed both the
summary video and the thumbnail grid, and the file hash runs in a background
thread while frames are decoded.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from videocore import (
    BatchResult,
    OutputSlot,
    VideoInfo,
    atomic_write,
    compose_grid,
    default_workers,
    evenly_spaced,
    iter_frames,
    plan_outputs,
    probe,
    remove_partials,
    run_parallel,
    sample_frames,
)
from videosemanticreducer import fingerprint as fp
from videosemanticreducer.info_card import render_info_card
from videosemanticreducer.manifest import (
    MANIFEST_SUFFIX,
    Source,
    build_manifest,
    is_complete,
    manifest_path,
    read_manifest,
    write_manifest,
)
from videosemanticreducer.report import write_report
from videosemanticreducer.summary import SummaryWriter, even_size

logger = logging.getLogger(__name__)

SNAPSHOT_MAX_SIDE = 1280
THUMB_MAX_SIDE = 240
THUMB_JPEG_QUALITY = 80
INFO_CARD_DURATION = 8.0
OUTPUT_DIR_SUFFIX = "_summary"


@dataclass(frozen=True)
class ProcessOptions:
    num_snapshots: int = 150
    snap_duration: float = 0.4
    thumb_cols: int = 12
    thumb_rows: int = 12
    summary: bool = True
    thumbnail: bool = True
    fingerprint: bool = True
    sampling_workers: int | None = None
    """Containers decoding in parallel per video; use 1 when processing videos in parallel."""

    def __post_init__(self) -> None:
        if self.num_snapshots <= 0:
            raise ValueError("num_snapshots must be greater than 0")
        if self.snap_duration <= 0:
            raise ValueError("snap_duration must be greater than 0")
        if self.thumb_cols <= 0 or self.thumb_rows <= 0:
            raise ValueError("thumb_cols and thumb_rows must be greater than 0")


@dataclass
class ProcessResult:
    video: Path
    output_dir: Path
    outputs: list[Path] = field(default_factory=list)
    """Artifacts written, in order; the manifest is not one of them."""
    manifest: Path | None = None
    timings: dict[str, float] = field(default_factory=dict)
    """Seconds spent in each step, for logging and benchmarks."""


def resolve_output_dir(video: Path, output: Path | None, *, batch: bool) -> Path:
    """Where the artifacts of `video` go.

    - no output given: ``<video folder>/<stem>_summary/``
    - output for a single video: ``<output>/``
    - output for a batch: ``<output>/<stem>_summary/``
    """
    if output is None:
        return video.parent / f"{video.stem}{OUTPUT_DIR_SUFFIX}"
    return output / f"{video.stem}{OUTPUT_DIR_SUFFIX}" if batch else output


def is_output_dir(path: Path) -> bool:
    """True for a ``<stem>_summary`` folder written by this tool.

    Recursive scans skip these folders, so the summary videos inside them are
    never archived again. A folder that only happens to end in ``_summary`` is
    still scanned unless it holds this tool's manifest, report or fingerprint
    (folders written before manifests existed have no manifest).
    """
    if not path.name.endswith(OUTPUT_DIR_SUFFIX):
        return False
    stem = path.name.removesuffix(OUTPUT_DIR_SUFFIX)
    markers = (f"{stem}{MANIFEST_SUFFIX}", f"{stem}_info.txt", f"{stem}.fingerprint.json")
    return any((path / marker).is_file() for marker in markers)


def snapshot_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spaced between 1% and 99% of the duration, skipping intros and credits."""
    return evenly_spaced(duration * 0.01, duration * 0.99, count)


def process_video(
    video: str | Path,
    output_dir: str | Path,
    options: ProcessOptions | None = None,
    *,
    name: str | None = None,
) -> ProcessResult:
    """Write the artifacts of `video` into `output_dir`, then its manifest.

    Artifacts are named after `name`, the video's stem by default; batches pass
    the full file name for videos whose stems collide (see ``videocore.outputs``).
    The manifest (``<name>.archive.json``) is written last, with
    ``complete: true`` only when every requested artifact was written.
    """
    options = options or ProcessOptions()
    video, output_dir = Path(video), Path(output_dir)
    result = ProcessResult(video=video, output_dir=output_dir)
    timer = _Timer(result.timings)
    started = time.perf_counter()

    with timer("probe"):
        info = probe(video)
    if not info.has_video:
        # Nothing could prove later that a copy matches this file, so it is not archived.
        raise ValueError(f"No video stream in {video}")
    stem = name or video.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    remove_partials(output_dir)  # left by a run that was killed
    result.manifest = manifest_path(output_dir, stem)
    # A rerun must not leave the previous "complete" record next to new, unfinished files.
    result.manifest.unlink(missing_ok=True)
    logger.info("Processing %s (%s, %s)", video, info.duration_hms, info.format_name)

    recorded = _recorded_options(options)
    try:
        sha256 = _write_artifacts(info, output_dir, stem, options, result, timer)
    except Exception as exc:
        manifest = build_manifest(
            Source.from_info(info), recorded, result.outputs, error=f"{type(exc).__name__}: {exc}"
        )
        try:
            write_manifest(manifest, result.manifest)
        except OSError:
            logger.warning("Could not write %s", result.manifest, exc_info=True)
        raise
    with timer("manifest"):
        manifest = build_manifest(Source.from_info(info, sha256), recorded, result.outputs)
        write_manifest(manifest, result.manifest)

    result.timings["total"] = time.perf_counter() - started
    logger.info("Finished %s in %.1f s -> %s", video, result.timings["total"], output_dir)
    return result


def _write_artifacts(
    info: VideoInfo,
    output_dir: Path,
    stem: str,
    options: ProcessOptions,
    result: ProcessResult,
    timer: _Timer,
) -> str | None:
    """Write the requested artifacts into `result.outputs`; return the file's SHA-256."""
    result.outputs.append(write_report(info, output_dir / f"{stem}_info.txt"))
    with ThreadPoolExecutor(max_workers=1) as hasher:
        sha_future = hasher.submit(fp.compute_sha256, info.path) if options.fingerprint else None

        if options.summary or options.thumbnail:
            with timer("snapshots"):
                summary_path = output_dir / f"{stem}_summary.mp4"
                thumbs = _snapshots_and_thumbs(info, summary_path, options, result)
            if options.thumbnail:
                with timer("thumbnail"):
                    result.outputs.append(
                        _write_thumbnail(info, thumbs, output_dir / f"{stem}_thumb.jpg", options)
                    )

        if sha_future is None:
            return None
        with timer("global_hashes"):
            global_hashes = fp.compute_global_hashes(info, workers=options.sampling_workers)
        with timer("keyframe_hashes"):
            keyframe_hashes = fp.compute_keyframe_hashes(info, workers=options.sampling_workers)
        with timer("sha256_wait"):
            sha256 = sha_future.result()
        fingerprint = fp.build_fingerprint(info, sha256, global_hashes, keyframe_hashes)
        # An incomplete fingerprint is never written: it would look like a valid archive.
        fp.check_complete(fingerprint)
        path = output_dir / f"{stem}.fingerprint.json"
        result.outputs.append(fp.save_fingerprint(fingerprint, path))
        return sha256


def _recorded_options(options: ProcessOptions) -> dict[str, object]:
    """The options that shape the artifacts; how many decoders ran does not."""
    recorded = dataclasses.asdict(options)
    del recorded["sampling_workers"]
    return recorded


def process_batch(
    videos: Sequence[Path],
    output: Path | None,
    options: ProcessOptions | None = None,
    *,
    source: Path | None = None,
    skip_complete: bool = False,
    workers: int | None = None,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[BatchResult]:
    """Process several videos in parallel processes; one failure does not stop the others.

    Each video gets its own ``<stem>_summary`` folder, next to it or inside `output`.
    With `source`, the folder that was scanned, its subfolders are recreated inside
    `output`. Videos whose stems collide in the same place use their full file
    name instead (``clip.mp4_summary``). Successful results carry the folder as
    their value. Raises ``videocore.OutputCollisionError`` before processing
    anything if two videos would still write the same folder.

    With `skip_complete`, videos already archived (see `archive_state`) are not
    processed again; they are returned with ``skipped=True`` and the state as
    their ``detail``. Progress is reported for the processed videos only.
    """
    slots = plan_outputs(videos, output=output, source=source)
    states = [
        archive_state(_slot_dir(slot), slot.name, options) if skip_complete else None
        for slot in slots
    ]
    skipped = [
        BatchResult(item=video, ok=True, value=_slot_dir(slot), skipped=True, detail=state)
        for video, slot, state in zip(videos, slots, states, strict=True)
        if state is not None
    ]
    if skipped:
        logger.info("Skipping %d videos that are already archived", len(skipped))
    todo = [i for i, state in enumerate(states) if state is None]
    options = options or ProcessOptions()
    workers = workers or default_workers()
    if workers > 1:
        # Videos already run in parallel; one decoder per video avoids oversubscription.
        options = dataclasses.replace(options, sampling_workers=1)
    task = functools.partial(_process_in_batch, options=options)
    try:
        processed = run_parallel(
            task,
            [videos[i] for i in todo],
            item_args=[slots[i] for i in todo],
            workers=workers,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
    finally:
        # Workers killed on cancel or Ctrl+C leave their unfinished files behind.
        for i in todo:
            remove_partials(_slot_dir(slots[i]))
    order = {video: index for index, video in enumerate(videos)}
    return sorted([*skipped, *processed], key=lambda result: order[result.item])


ARCHIVE_COMPLETE = "complete"
ARCHIVE_OUTDATED = "outdated"
_ARTIFACT_TOGGLES = ("summary", "thumbnail", "fingerprint")


def archive_state(output_dir: Path, name: str, options: ProcessOptions) -> str | None:
    """Whether the video named `name` still needs archiving with `options`.

    - ``"complete"``: a complete archive made with the same options.
    - ``"outdated"``: complete and with every requested artifact, but made with
      other settings (e.g. another number of snapshots). It is not redone unless
      the user asks, since that can mean re-archiving a whole library.
    - None: it must be archived, because there is no complete manifest or an
      artifact requested now (say, the fingerprint) was not made back then.
    """
    path = manifest_path(output_dir, name)
    if not is_complete(path):
        return None
    recorded = (read_manifest(path) or {}).get("options") or {}
    wanted = _recorded_options(options)
    if any(wanted[toggle] and not recorded.get(toggle) for toggle in _ARTIFACT_TOGGLES):
        return None
    settings = [key for key in wanted if key not in _ARTIFACT_TOGGLES]
    same = all(recorded.get(key) == wanted[key] for key in settings)
    return ARCHIVE_COMPLETE if same else ARCHIVE_OUTDATED


def _slot_dir(slot: OutputSlot) -> Path:
    return slot.folder / f"{slot.name}{OUTPUT_DIR_SUFFIX}"


def _process_in_batch(video: Path, slot: OutputSlot, *, options: ProcessOptions) -> Path:
    # Module-level so it can be pickled into worker processes.
    out_dir = _slot_dir(slot)
    process_video(video, out_dir, options, name=slot.name)
    return out_dir


def _snapshots_and_thumbs(
    info: VideoInfo, summary_path: Path, options: ProcessOptions, result: ProcessResult
) -> list[Image.Image | None]:
    """Decode the snapshots once: encode the summary and keep small copies for the grid.

    Returns the grid thumbnails (empty when no thumbnail grid was requested).
    """
    assert info.video is not None
    cells = options.thumb_cols * options.thumb_rows
    thumb_size = even_size(info.video.width, info.video.height, THUMB_MAX_SIDE)
    if not options.summary:
        timestamps = snapshot_timestamps(info.duration, cells)
        return sample_frames(
            info.path, timestamps, size=thumb_size, workers=options.sampling_workers
        )

    snap_size = even_size(info.video.width, info.video.height, SNAPSHOT_MAX_SIDE)
    timestamps = snapshot_timestamps(info.duration, options.num_snapshots)
    # With enough snapshots, grid cell i shows the snapshot nearest to its own evenly
    # spaced position; otherwise the grid is sampled separately afterwards.
    reuse_for_grid = options.thumbnail and options.num_snapshots >= cells
    grid_sources = _grid_sources(options.num_snapshots, cells) if reuse_for_grid else {}
    thumbs: list[Image.Image | None] = [None] * cells if reuse_for_grid else []
    decoded = 0

    with SummaryWriter(summary_path, snap_size, still_duration=options.snap_duration) as writer:
        writer.add(render_info_card(info, snap_size), duration=INFO_CARD_DURATION)
        # Frames arrive in order while the next ones are decoded, so encoding overlaps
        # decoding and only a few full-size frames are in memory at a time.
        frames = iter_frames(
            info.path, timestamps, size=snap_size, workers=options.sampling_workers
        )
        for index, image in frames:
            if image is None:
                continue
            decoded += 1
            writer.add(image)
            for cell in grid_sources.get(index, ()):
                thumbs[cell] = image.resize(thumb_size, Image.Resampling.BILINEAR)
        if decoded == 0:
            raise RuntimeError(f"No snapshot could be decoded from {info.path}")

    if decoded < len(timestamps):
        logger.warning("Decoded %d/%d snapshots of %s", decoded, len(timestamps), info.path)
    result.outputs.append(summary_path)

    if options.thumbnail and not reuse_for_grid:
        thumbs = sample_frames(
            info.path,
            snapshot_timestamps(info.duration, cells),
            size=thumb_size,
            workers=options.sampling_workers,
        )
    return thumbs


def _grid_sources(snapshots: int, cells: int) -> dict[int, list[int]]:
    """Map snapshot index -> grid cells it fills (nearest evenly spaced position)."""
    sources: dict[int, list[int]] = {}
    for cell in range(cells):
        index = 0 if cells == 1 else round(cell * (snapshots - 1) / (cells - 1))
        sources.setdefault(index, []).append(cell)
    return sources


def _write_thumbnail(
    info: VideoInfo, thumbs: list[Image.Image | None], path: Path, options: ProcessOptions
) -> Path:
    decoded = sum(thumb is not None for thumb in thumbs)
    if decoded == 0:
        raise RuntimeError(f"No thumbnail could be decoded from {info.path}")
    if decoded < len(thumbs):
        logger.warning(
            "Thumbnail grid of %s has %d/%d cells; the rest are black",
            info.path,
            decoded,
            len(thumbs),
        )
    assert info.video is not None
    cell_size = even_size(info.video.width, info.video.height, THUMB_MAX_SIDE)
    grid = compose_grid(thumbs, options.thumb_cols, cell_size=cell_size)
    with atomic_write(path) as part:
        grid.save(part, "JPEG", quality=THUMB_JPEG_QUALITY)
    return path


class _Timer:
    def __init__(self, timings: dict[str, float]) -> None:
        self.timings = timings

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + time.perf_counter() - start
