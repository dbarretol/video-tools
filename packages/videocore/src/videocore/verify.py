"""Checks run on outputs right after they are written.

A task reads back what it wrote, so a missing or broken output is never
reported as a success. Each check either raises `OutputError` (the output is
unusable: the video FAILED) or returns a problem, a sentence saying how the
output is incomplete (the video is DEGRADED). See ``videocore.jobs.Task.verify``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from videocore.errors import VideoOpenError
from videocore.probe import probe

DEGRADED_BELOW = 0.9
"""Outputs with fewer than this share of their frames decoded are DEGRADED."""


class OutputError(RuntimeError):
    """An output is missing or unreadable after being written."""


@dataclass
class FrameCount:
    """How many of the requested frames of an output could be decoded."""

    decoded: int = 0
    total: int = 0

    def problem(self, what: str) -> str | None:
        """A problem when too few frames were decoded, e.g. "17/20 thumbnails decoded"."""
        if self.total and self.decoded < self.total * DEGRADED_BELOW:
            return f"{what}: only {self.decoded}/{self.total} frames could be decoded"
        return None


def check_image(path: Path, size: tuple[int, int] | None = None) -> tuple[int, int]:
    """`path` is an image Pillow can read, of `size` (width, height) when given.

    Returns its size.
    """
    check_file(path)
    try:
        with Image.open(path) as image:
            actual = image.size
            image.verify()
    except (OSError, SyntaxError) as exc:  # Pillow raises SyntaxError for some broken files
        raise OutputError(f"{path.name} is not a readable image: {exc}") from exc
    if size is not None and actual != tuple(size):
        raise OutputError(f"{path.name} is {actual[0]}x{actual[1]}, expected {size[0]}x{size[1]}")
    return actual


def check_video(path: Path, duration: float | None = None, tolerance: float = 0.1) -> None:
    """`path` opens as a video with a video stream, `duration` seconds long (± `tolerance`)."""
    check_file(path)
    try:
        info = probe(path)
    except VideoOpenError as exc:
        raise OutputError(f"{path.name} is not a readable video: {exc}") from exc
    if not info.has_video:
        raise OutputError(f"{path.name} has no video stream")
    if duration is not None and abs(info.duration - duration) > max(1.0, duration * tolerance):
        raise OutputError(
            f"{path.name} lasts {info.duration:.1f} s, expected about {duration:.1f} s"
        )


def check_file(path: Path) -> None:
    """`path` was written and is not empty."""
    if not path.is_file():
        raise OutputError(f"{path.name} was not written")
    if path.stat().st_size == 0:
        raise OutputError(f"{path.name} is empty")
