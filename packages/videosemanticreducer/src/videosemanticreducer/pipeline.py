"""Process one video into its archive artifacts.

For ``movie.mp4`` the output folder gets:

- ``movie_info.txt``: metadata report.
- ``movie_summary.mp4``: info card followed by evenly spaced snapshots.
- ``movie_thumb.jpg``: grid of thumbnails.
- ``movie.fingerprint.json``: perceptual fingerprint.

The video is decoded as few times as possible: the snapshots feed both the
summary video and the thumbnail grid, and the file hash runs in a background
thread while frames are decoded.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from videocore import VideoInfo, compose_grid, evenly_spaced, probe, sample_frames
from videosemanticreducer import fingerprint as fp
from videosemanticreducer.info_card import render_info_card
from videosemanticreducer.report import write_report
from videosemanticreducer.summary import SummaryWriter, even_size

logger = logging.getLogger(__name__)

SNAPSHOT_MAX_SIDE = 1280
THUMB_MAX_SIDE = 240
THUMB_JPEG_QUALITY = 80
INFO_CARD_DURATION = 8.0
# Snapshots are decoded and encoded in batches to bound memory use (a 1280x720 RGB
# frame takes 2.7 MB).
SNAPSHOT_BATCH = 32


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
    timings: dict[str, float] = field(default_factory=dict)
    """Seconds spent in each step, for logging and benchmarks."""


def resolve_output_dir(video: Path, output: Path | None, *, batch: bool) -> Path:
    """Where the artifacts of `video` go.

    - no output given: ``<video folder>/<stem>_summary/``
    - output for a single video: ``<output>/``
    - output for a batch: ``<output>/<stem>_summary/``
    """
    if output is None:
        return video.parent / f"{video.stem}_summary"
    return output / f"{video.stem}_summary" if batch else output


def snapshot_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spaced between 1% and 99% of the duration, skipping intros and credits."""
    return evenly_spaced(duration * 0.01, duration * 0.99, count)


def process_video(
    video: str | Path, output_dir: str | Path, options: ProcessOptions | None = None
) -> ProcessResult:
    options = options or ProcessOptions()
    video, output_dir = Path(video), Path(output_dir)
    result = ProcessResult(video=video, output_dir=output_dir)
    timer = _Timer(result.timings)
    started = time.perf_counter()

    with timer("probe"):
        info = probe(video)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    result.outputs.append(write_report(info, output_dir / f"{stem}_info.txt"))
    logger.info("Processing %s (%s, %s)", video, info.duration_hms, info.format_name)

    with ThreadPoolExecutor(max_workers=1) as hasher:
        sha_future = hasher.submit(fp.compute_sha256, video) if options.fingerprint else None

        if info.has_video:
            with timer("snapshots"):
                summary_path = output_dir / f"{stem}_summary.mp4"
                thumbs = _snapshots_and_thumbs(info, summary_path, options, result)
            if options.thumbnail:
                with timer("thumbnail"):
                    result.outputs.append(_write_thumbnail(info, thumbs, output_dir, options))
        else:
            logger.info("%s has no video stream; only metadata and file hash", video)

        if options.fingerprint:
            global_hashes, keyframe_hashes = None, []
            if info.has_video:
                with timer("global_hashes"):
                    global_hashes = fp.compute_global_hashes(info, workers=options.sampling_workers)
                with timer("keyframe_hashes"):
                    keyframe_hashes = fp.compute_keyframe_hashes(
                        info, workers=options.sampling_workers
                    )
            with timer("sha256_wait"):
                sha256 = sha_future.result()
            fingerprint = fp.build_fingerprint(info, sha256, global_hashes, keyframe_hashes)
            path = output_dir / f"{stem}.fingerprint.json"
            result.outputs.append(fp.save_fingerprint(fingerprint, path))

    result.timings["total"] = time.perf_counter() - started
    logger.info("Finished %s in %.1f s -> %s", video, result.timings["total"], output_dir)
    return result


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
        if not options.thumbnail:
            return []
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
        for start in range(0, len(timestamps), SNAPSHOT_BATCH):
            batch = timestamps[start : start + SNAPSHOT_BATCH]
            images = sample_frames(
                info.path, batch, size=snap_size, workers=options.sampling_workers
            )
            for offset, image in enumerate(images):
                if image is None:
                    continue
                decoded += 1
                writer.add(image)
                for cell in grid_sources.get(start + offset, ()):
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
    info: VideoInfo, thumbs: list[Image.Image | None], output_dir: Path, options: ProcessOptions
) -> Path:
    if not any(thumb is not None for thumb in thumbs):
        raise RuntimeError(f"No thumbnail could be decoded from {info.path}")
    assert info.video is not None
    cell_size = even_size(info.video.width, info.video.height, THUMB_MAX_SIDE)
    grid = compose_grid(thumbs, options.thumb_cols, cell_size=cell_size)
    path = output_dir / f"{info.path.stem}_thumb.jpg"
    grid.save(path, "JPEG", quality=THUMB_JPEG_QUALITY)
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
