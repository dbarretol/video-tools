"""Summary video: the info card followed by one still per snapshot, encoded in one pass.

Every image is shown for the same time, so the video uses a constant frame rate
of one frame per snapshot (2.5 fps for 0.4 s snapshots) and the card is simply
repeated. x264 with ``tune=stillimage`` turns those repeats into nearly empty
frames, so a low frame rate costs no quality and encodes far faster than
re-encoding at 25 fps.
"""

from __future__ import annotations

import contextlib
import os
from fractions import Fraction
from pathlib import Path
from types import TracebackType

import av
from PIL import Image

from videocore import part_path

DEFAULT_ENCODER_OPTIONS = {"crf": "26", "preset": "veryfast", "tune": "stillimage"}


def even_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """Scale (width, height) so the longer side is at most `max_side`, with even dimensions.

    Never upscales; H.264 with yuv420p needs even sizes.
    """
    scale = min(1.0, max_side / max(width, height))
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


class SummaryWriter:
    """Encode stills of a fixed size into an H.264 MP4, each shown for `still_duration`.

    The video is encoded into ``<path>.part`` and only renamed to `path` when the
    writer is closed without an error, so `path` never holds a truncated video.
    """

    def __init__(
        self,
        path: Path,
        size: tuple[int, int],
        *,
        still_duration: float,
        encoder_options: dict[str, str] | None = None,
    ) -> None:
        if still_duration <= 0:
            raise ValueError("still_duration must be greater than 0")
        self.path = path
        self.size = size
        self.still_duration = still_duration
        self.frames_written = 0

        rate = 1 / Fraction(still_duration).limit_denominator(1000)
        self._part = part_path(path)
        self._container = av.open(
            str(self._part),
            mode="w",
            format="mp4",  # the .part name does not tell the muxer which format
            container_options={"movflags": "+faststart"},
        )
        self._stream = self._container.add_stream("libx264", rate=rate)
        self._stream.width, self._stream.height = size
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = dict(encoder_options or DEFAULT_ENCODER_OPTIONS)

    def add(self, image: Image.Image, *, duration: float | None = None) -> None:
        """Append `image`, shown for `duration` seconds (default: one still)."""
        if image.size != self.size:
            image = image.resize(self.size, Image.Resampling.LANCZOS)
        repeats = 1 if duration is None else max(1, round(duration / self.still_duration))
        frame = av.VideoFrame.from_image(image.convert("RGB"))
        for _ in range(repeats):
            frame.pts = self.frames_written
            self._container.mux(self._stream.encode(frame))
            self.frames_written += 1

    def close(self) -> None:
        """Finish the video and move it to its final path."""
        if self._container is None:
            return
        try:
            self._container.mux(self._stream.encode())
        finally:
            self._container.close()
            self._container = None
        os.replace(self._part, self.path)

    def discard(self) -> None:
        """Stop encoding and delete the unfinished video."""
        if self._container is not None:
            # Closing a half-written file may fail too; the error that led here matters more.
            with contextlib.suppress(Exception):
                self._container.close()
            self._container = None
        self._part.unlink(missing_ok=True)

    def __enter__(self) -> SummaryWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.discard()
            return
        try:
            self.close()
        except BaseException:
            self.discard()
            raise
