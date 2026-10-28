"""Frame sampling: decode the frames shown at a list of timestamps.

Two modes:

- ``"keyframe"``: seek to the keyframe at or before each timestamp and decode only
  that frame. One decode per sample, so it is fast on long videos; the frame may
  be up to one GOP earlier than requested. Good for thumbnails and previews.
- ``"precise"``: seek to that keyframe and decode forward to the frame nearest the
  timestamp. Slower (up to a GOP of decodes per sample) but independent of where
  the encoder placed keyframes, which matters for perceptual fingerprints.

Threading is tuned per mode (measured on a 1080p60 H.264 file): keyframe sampling
uses slice threads in the decoder plus several containers decoding in parallel
threads, while precise sampling uses the decoder's frame threads, which already
keep every core busy while decoding a GOP.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import av
from PIL import Image

from videocore.errors import VideoOpenError

Mode = Literal["keyframe", "precise"]

MAX_KEYFRAME_WORKERS = 4


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
    workers: int | None = None,
) -> list[Image.Image | None]:
    """Decode one RGB frame per timestamp (seconds).

    Returns a list aligned with `timestamps`; an entry is None when no frame
    could be decoded there, so callers decide how to handle gaps. `size` scales
    frames to (width, height) inside the decoder, which is much cheaper than
    resizing full-resolution images afterwards.

    `workers` is the number of containers decoding in parallel threads. It
    defaults to up to 4 in keyframe mode and 1 in precise mode; pass 1 when the
    caller already processes several videos in parallel.
    """
    if mode not in ("keyframe", "precise"):
        raise ValueError(f"Unknown sampling mode: {mode!r}")
    path = os.fspath(path)

    order = sorted(range(len(timestamps)), key=timestamps.__getitem__)
    if workers is None:
        workers = _default_workers(mode)
    workers = max(1, min(workers, len(order)))

    # Contiguous chunks keep each container seeking forward through its own region.
    chunk_size = -(-len(order) // workers) if order else 0
    chunks = [order[i : i + chunk_size] for i in range(0, len(order), chunk_size or 1)]

    results: list[Image.Image | None] = [None] * len(timestamps)
    if len(chunks) <= 1:
        decoded = [_sample_chunk(path, timestamps, chunk, mode, size) for chunk in chunks]
    else:
        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            decoded = list(
                pool.map(lambda chunk: _sample_chunk(path, timestamps, chunk, mode, size), chunks)
            )
    for chunk_result in decoded:
        for index, image in chunk_result:
            results[index] = image
    return results


def _default_workers(mode: Mode) -> int:
    if mode == "precise":
        return 1
    return max(1, min(MAX_KEYFRAME_WORKERS, (os.cpu_count() or 2) // 2))


def _sample_chunk(
    path: str,
    timestamps: Sequence[float],
    indices: Sequence[int],
    mode: Mode,
    size: tuple[int, int] | None,
) -> list[tuple[int, Image.Image | None]]:
    try:
        container = av.open(path)
    except av.FFmpegError as exc:
        raise VideoOpenError(f"Could not open video: {path}") from exc

    with container:
        if not container.streams.video:
            raise VideoOpenError(f"No video stream in {path}")
        stream = container.streams.video[0]
        if mode == "keyframe":
            stream.thread_type = "SLICE"
            stream.codec_context.skip_frame = "NONKEY"
        else:
            stream.thread_type = "AUTO"
        frame_period = 1 / float(stream.average_rate or stream.guessed_rate or 30)

        out: list[tuple[int, Image.Image | None]] = []
        for index in indices:
            ts = timestamps[index]
            try:
                container.seek(int(max(ts, 0) / stream.time_base), stream=stream, backward=True)
                frame = _decode_at(container, stream, ts, mode, frame_period)
            except av.FFmpegError:
                frame = None
            out.append((index, _to_image(frame, size) if frame is not None else None))
        return out


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
