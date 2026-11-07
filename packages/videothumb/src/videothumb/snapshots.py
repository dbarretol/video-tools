"""Frames: N evenly spaced full-resolution frames saved as individual JPEGs.

Each file says which video and which moment it is, so frames can be moved or
mixed with others: ``clip_f001_00h12m34s.jpg`` is the first frame of
``clip.mp4``, taken at 12 minutes 34 seconds. (The module and its names keep
the old word "snapshots" for the tkinter app.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from videocore import (
    NoFramesError,
    NoVideoStreamError,
    atomic_write,
    evenly_spaced,
    iter_frames,
    probe,
)
from videocore.options import option, validate
from videocore.verify import FrameCount
from videothumb.common import Sampling, jpeg_quality_option, sampling_option

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotOptions:
    count: int = option(10, "frames to save", gt=0, label="Frames", group="frames")
    jpeg_quality: int = jpeg_quality_option()
    sampling: Sampling = sampling_option()

    def __post_init__(self) -> None:
        validate(self)


FOLDER_SUFFIX = "_frames"


def default_snapshot_dir(video: Path) -> Path:
    """``<stem>_frames`` next to the video."""
    return video.parent / f"{video.stem}{FOLDER_SUFFIX}"


def frame_name(name: str, number: int, seconds: float, pad: int = 3) -> str:
    """``clip_f001_00h12m34s.jpg``: frame `number` of video `name`, taken at `seconds`."""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{name}_f{number:0{pad}d}_{hours:02d}h{minutes:02d}m{secs:02d}s.jpg"


def make_snapshots(
    video: str | Path,
    output_dir: str | Path,
    options: SnapshotOptions | None = None,
    *,
    decoders: int | None = None,
    counts: FrameCount | None = None,
    name: str | None = None,
) -> list[Path]:
    """Save `options.count` frames in `output_dir`, named by `frame_name`.

    `name` starts every file name (default: the video's stem).

    `decoders` limits the containers decoding in parallel (see ``videocore.iter_frames``).
    `counts`, when given, receives how many snapshots could be decoded.

    Frames that cannot be decoded are skipped, so fewer files may be written;
    their numbers stay unused so names always reflect the position in the video.
    The time in each name is that of the frame actually saved.
    """
    options = options or SnapshotOptions()
    info = probe(video)
    if info.video is None:
        raise NoVideoStreamError(f"No video stream in {info.path}")

    timestamps = evenly_spaced(0, info.duration, options.count, endpoints=False)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pad = max(3, len(str(options.count)))

    saved = []
    # Full-resolution frames are written as they are decoded instead of all being held in memory.
    frames = iter_frames(
        info.path,
        timestamps,
        mode=options.sampling,
        workers=decoders,
        stage="frames",
        fallback=True,
    )
    name = name or Path(video).stem
    for index, image in frames:
        if image is None:
            continue
        seconds = image.info.get("time", timestamps[index])
        path = output_dir / frame_name(name, index + 1, seconds, pad)
        with atomic_write(path) as part:
            image.save(part, "JPEG", quality=options.jpeg_quality)
        saved.append(path)
    if counts is not None:
        counts.decoded, counts.total = len(saved), options.count
    if not saved:
        raise NoFramesError(f"No frame could be decoded from {info.path}")
    if len(saved) < options.count:
        logger.warning(
            "Saved %d/%d frames of %s; the others could not be decoded",
            len(saved),
            options.count,
            info.path,
        )
    return saved
