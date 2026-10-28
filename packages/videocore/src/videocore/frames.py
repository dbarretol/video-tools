"""Frame sampling: decode the frames shown at a list of timestamps.

Two modes:

- ``"keyframe"``: seek to the keyframe at or before each timestamp and decode only
  that frame. One decode per sample, so it is fast on long videos; the frame may
  be up to one GOP earlier than requested. Good for thumbnails and previews.
- ``"precise"``: seek to that keyframe and decode forward to the frame nearest the
  timestamp. Slower (up to a GOP of decodes per sample) but independent of where
  the encoder placed keyframes, which matters for perceptual fingerprints.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Literal

import av
from PIL import Image

from videocore.errors import VideoOpenError

Mode = Literal["keyframe", "precise"]


def evenly_spaced(start: float, stop: float, count: int, *, endpoints: bool = True) -> list[float]:
    """Return `count` evenly spaced timestamps in [start, stop].

    With ``endpoints=False`` the bounds themselves are excluded, which avoids
    black or transition frames at the very start and end of a video.
    """
    if count <= 0:
        raise ValueError("count must be greater than 0")
    if not endpoints:
        step = (stop - start) / (count + 1)
        return [start + step * (i + 1) for i in range(count)]
    if count == 1:
        return [start]
    step = (stop - start) / (count - 1)
    return [start + step * i for i in range(count)]


def sample_frames(
    path: str | os.PathLike[str],
    timestamps: Sequence[float],
    *,
    mode: Mode = "keyframe",
    size: tuple[int, int] | None = None,
) -> list[Image.Image | None]:
    """Decode one RGB frame per timestamp (seconds), opening the video once.

    Returns a list aligned with `timestamps`; an entry is None when no frame
    could be decoded there, so callers decide how to handle gaps. `size` scales
    frames to (width, height) inside the decoder, which is much cheaper than
    resizing full-resolution images afterwards.
    """
    if mode not in ("keyframe", "precise"):
        raise ValueError(f"Unknown sampling mode: {mode!r}")

    try:
        container = av.open(os.fspath(path))
    except av.FFmpegError as exc:
        raise VideoOpenError(f"Could not open video: {path}") from exc

    with container:
        if not container.streams.video:
            raise VideoOpenError(f"No video stream in {path}")
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        if mode == "keyframe":
            stream.codec_context.skip_frame = "NONKEY"
        frame_period = 1 / float(stream.average_rate or stream.guessed_rate or 30)

        results: list[Image.Image | None] = [None] * len(timestamps)
        for index in sorted(range(len(timestamps)), key=timestamps.__getitem__):
            ts = timestamps[index]
            try:
                container.seek(int(max(ts, 0) / stream.time_base), stream=stream, backward=True)
                frame = _decode_at(container, stream, ts, mode, frame_period)
            except av.FFmpegError:
                frame = None
            if frame is not None:
                results[index] = _to_image(frame, size)
        return results


def _decode_at(
    container: av.container.InputContainer,
    stream: av.VideoStream,
    ts: float,
    mode: Mode,
    frame_period: float,
) -> av.VideoFrame | None:
    if mode == "keyframe":
        return next(container.decode(stream), None)

    # Return the frame nearest to `ts`: the first one whose time is past ts - half a frame.
    last = None
    for frame in container.decode(stream):
        if frame.time is None:
            continue
        last = frame
        if frame.time >= ts - frame_period / 2:
            return frame
    return last


def _to_image(frame: av.VideoFrame, size: tuple[int, int] | None) -> Image.Image:
    if size is None:
        return frame.to_image()
    width, height = size
    return frame.reformat(width=width, height=height, format="rgb24").to_image()
