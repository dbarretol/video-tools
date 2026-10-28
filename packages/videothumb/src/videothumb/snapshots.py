"""Snapshots: N evenly spaced full-resolution frames saved as individual JPEGs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from videocore import evenly_spaced, iter_frames, probe
from videothumb.common import Sampling


@dataclass(frozen=True)
class SnapshotOptions:
    count: int = 10
    jpeg_quality: int = 90
    sampling: Sampling = "keyframe"
    workers: int | None = None
    """Containers decoding in parallel; use 1 when processing videos in parallel."""

    def __post_init__(self) -> None:
        if self.count <= 0:
            raise ValueError("count must be greater than 0")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")


def default_snapshot_dir(video: Path) -> Path:
    """A folder named after the video, next to it."""
    return video.parent / video.stem


def make_snapshots(
    video: str | Path, output_dir: str | Path, options: SnapshotOptions | None = None
) -> list[Path]:
    """Save `options.count` snapshots as ``snapshot_001.jpg``... in `output_dir`.

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
    frames = iter_frames(info.path, timestamps, mode=options.sampling, workers=options.workers)
    for index, image in frames:
        if image is None:
            continue
        path = output_dir / f"snapshot_{index + 1:0{pad}d}.jpg"
        image.save(path, "JPEG", quality=options.jpeg_quality)
        saved.append(path)
    if not saved:
        raise RuntimeError(f"No frame could be decoded from {info.path}")
    return saved
