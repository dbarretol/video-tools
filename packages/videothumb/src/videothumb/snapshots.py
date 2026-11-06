"""Snapshots: N evenly spaced full-resolution frames saved as individual JPEGs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from videocore import atomic_write, evenly_spaced, iter_frames, probe
from videocore.options import option, validate
from videocore.verify import FrameCount
from videothumb.common import Sampling, jpeg_quality_option, sampling_option

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotOptions:
    count: int = option(10, "snapshots to save", gt=0, label="Snapshots", group="snapshots")
    jpeg_quality: int = jpeg_quality_option()
    sampling: Sampling = sampling_option()

    def __post_init__(self) -> None:
        validate(self)


def default_snapshot_dir(video: Path) -> Path:
    """A folder named after the video, next to it."""
    return video.parent / video.stem


def make_snapshots(
    video: str | Path,
    output_dir: str | Path,
    options: SnapshotOptions | None = None,
    *,
    decoders: int | None = None,
    counts: FrameCount | None = None,
) -> list[Path]:
    """Save `options.count` snapshots as ``snapshot_001.jpg``... in `output_dir`.

    `decoders` limits the containers decoding in parallel (see ``videocore.iter_frames``).
    `counts`, when given, receives how many snapshots could be decoded.

    Frames that cannot be decoded are skipped, so fewer files may be written;
    their numbers stay unused so names always reflect the position in the video.
    """
    options = options or SnapshotOptions()
    info = probe(video)
    if info.video is None:
        raise ValueError(f"No video stream in {info.path}")

    timestamps = evenly_spaced(0, info.duration, options.count, endpoints=False)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pad = max(3, len(str(options.count)))

    saved = []
    # Full-resolution frames are written as they are decoded instead of all being held in memory.
    frames = iter_frames(
        info.path, timestamps, mode=options.sampling, workers=decoders, stage="snapshots"
    )
    for index, image in frames:
        if image is None:
            continue
        path = output_dir / f"snapshot_{index + 1:0{pad}d}.jpg"
        with atomic_write(path) as part:
            image.save(part, "JPEG", quality=options.jpeg_quality)
        saved.append(path)
    if counts is not None:
        counts.decoded, counts.total = len(saved), options.count
    if not saved:
        raise RuntimeError(f"No frame could be decoded from {info.path}")
    if len(saved) < options.count:
        logger.warning(
            "Saved %d/%d snapshots of %s; the other frames could not be decoded",
            len(saved),
            options.count,
            info.path,
        )
    return saved
