"""Frame sampling: decode the frames shown at a list of timestamps.

Two modes:

- ``"keyframe"``: seek to the keyframe at or before each timestamp and decode only
  that frame. One decode per sample, so it is fast on long videos; the frame may
  be up to one GOP earlier than requested. Good for thumbnails and previews.
- ``"precise"``: seek to that keyframe and decode forward to the frame nearest the
  timestamp. Slower (up to a GOP of decodes per sample) but independent of where
  the encoder placed keyframes, which matters for perceptual fingerprints.

``draft=True`` trades image quality for speed: the decoder skips the deblocking
filter and, in precise mode, frames no other frame depends on (most B-frames),
so a frame may come from a frame or two away and look slightly blocky. On a
1080p60 H.264 file this makes precise sampling about 40% faster; at hashing
sizes the difference is invisible.

Threading is tuned per mode (measured on a 1080p60 H.264 file): keyframe sampling
uses slice threads in the decoder plus several containers decoding in parallel
threads, while precise sampling uses the decoder's frame threads, which already
keep every core busy while decoding a GOP. Parallel containers take the sorted
timestamps in turns, so frames come out roughly in order and `iter_frames` can
hand them over as soon as they are ready with a small reorder buffer.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from typing import Literal

import av
from PIL import Image

from videocore.errors import VideoOpenError

Mode = Literal["keyframe", "precise"]

MAX_KEYFRAME_WORKERS = 4
# Frames each parallel container may decode ahead of the consumer.
READ_AHEAD_PER_WORKER = 2


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
    draft: bool = False,
) -> list[Image.Image | None]:
    """Decode one RGB frame per timestamp (seconds).

    Returns a list aligned with `timestamps`; an entry is None when no frame
    could be decoded there, so callers decide how to handle gaps. The actual
    time of each frame (seconds, possibly earlier than requested in keyframe
    mode) is stored in ``image.info["time"]``. `size` scales
    frames to (width, height) inside the decoder, which is much cheaper than
    resizing full-resolution images afterwards.

    `workers` is the number of containers decoding in parallel threads. It
    defaults to up to 4 in keyframe mode and 1 in precise mode; pass 1 when the
    caller already processes several videos in parallel.
    """
    results: list[Image.Image | None] = [None] * len(timestamps)
    frames = iter_frames(path, timestamps, mode=mode, size=size, workers=workers, draft=draft)
    for index, image in frames:
        results[index] = image
    return results


def iter_frames(
    path: str | os.PathLike[str],
    timestamps: Sequence[float],
    *,
    mode: Mode = "keyframe",
    size: tuple[int, int] | None = None,
    workers: int | None = None,
    draft: bool = False,
) -> Iterator[tuple[int, Image.Image | None]]:
    """Like `sample_frames`, but yield ``(index, frame)`` pairs in timestamp order.

    `index` is the position in `timestamps`. Frames are yielded as soon as they
    are decoded and only a few are buffered, so a long list of large frames can
    be processed (for example encoded) while the next ones are being decoded,
    without holding them all in memory.
    """
    if mode not in ("keyframe", "precise"):
        raise ValueError(f"Unknown sampling mode: {mode!r}")
    order = sorted(range(len(timestamps)), key=timestamps.__getitem__)
    if workers is None:
        workers = _default_workers(mode)
    workers = max(1, min(workers, len(order)))
    job = _Job(os.fspath(path), timestamps, mode, size, draft)
    if workers == 1:
        return job.decode(order)
    return _decode_parallel(job, order, workers)


def _default_workers(mode: Mode) -> int:
    if mode == "precise":
        return 1
    return max(1, min(MAX_KEYFRAME_WORKERS, (os.cpu_count() or 2) // 2))


class _Job:
    """What to decode: shared by every container working on the same request."""

    def __init__(
        self,
        path: str,
        timestamps: Sequence[float],
        mode: Mode,
        size: tuple[int, int] | None,
        draft: bool,
    ) -> None:
        self.path = path
        self.timestamps = timestamps
        self.mode = mode
        self.size = size
        self.draft = draft

    def decode(self, indices: Sequence[int]) -> Iterator[tuple[int, Image.Image | None]]:
        """Open one container and decode `indices` in the given order."""
        if not indices:
            return
        try:
            container = av.open(self.path)
        except av.FFmpegError as exc:
            raise VideoOpenError(f"Could not open video: {self.path}") from exc

        with container:
            if not container.streams.video:
                raise VideoOpenError(f"No video stream in {self.path}")
            stream = container.streams.video[0]
            self._configure(stream)
            frame_period = 1 / float(stream.average_rate or stream.guessed_rate or 30)

            for index in indices:
                ts = self.timestamps[index]
                try:
                    container.seek(int(max(ts, 0) / stream.time_base), stream=stream, backward=True)
                    frame = self._decode_at(container, stream, ts, frame_period)
                except av.FFmpegError:
                    frame = None
                yield index, (_to_image(frame, self.size) if frame is not None else None)

    def _configure(self, stream: av.VideoStream) -> None:
        ctx = stream.codec_context
        if self.mode == "keyframe":
            stream.thread_type = "SLICE"
            ctx.skip_frame = "NONKEY"
        else:
            stream.thread_type = "AUTO"
            if self.draft:
                ctx.skip_frame = "NONREF"
        if self.draft:
            ctx.options = {**ctx.options, "skip_loop_filter": "all"}

    def _decode_at(
        self,
        container: av.container.InputContainer,
        stream: av.VideoStream,
        ts: float,
        frame_period: float,
    ) -> av.VideoFrame | None:
        if self.mode == "keyframe":
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


def _decode_parallel(
    job: _Job, order: list[int], workers: int
) -> Iterator[tuple[int, Image.Image | None]]:
    # Container k decodes ranks k, k + workers, ...: every container seeks forward
    # through the whole range and together they produce frames close to in order.
    buffer = _ReorderBuffer(window=workers * READ_AHEAD_PER_WORKER)

    def work(first_rank: int) -> None:
        ranks = range(first_rank, len(order), workers)
        try:
            with closing(job.decode([order[r] for r in ranks])) as frames:
                for rank, item in zip(ranks, frames, strict=True):
                    if not buffer.put(rank, item):
                        return
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer thread
            buffer.fail(exc)

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        for first_rank in range(workers):
            pool.submit(work, first_rank)
        for _ in order:
            yield buffer.get()
    finally:
        buffer.close()
        pool.shutdown(wait=True)


class _ReorderBuffer:
    """Hand results produced out of order by several threads back in rank order.

    Producers block while they are more than `window` ranks ahead of the consumer,
    which bounds memory use.
    """

    def __init__(self, window: int) -> None:
        self._cond = threading.Condition()
        self._items: dict[int, tuple[int, Image.Image | None]] = {}
        self._next = 0
        self._window = window
        self._error: BaseException | None = None
        self._closed = False

    def put(self, rank: int, item: tuple[int, Image.Image | None]) -> bool:
        """Store a result; return False if the consumer is gone and work should stop."""
        with self._cond:
            while not self._closed and rank >= self._next + self._window:
                self._cond.wait()
            if self._closed:
                return False
            self._items[rank] = item
            self._cond.notify_all()
            return True

    def fail(self, exc: BaseException) -> None:
        with self._cond:
            if self._error is None:
                self._error = exc
            self._cond.notify_all()

    def get(self) -> tuple[int, Image.Image | None]:
        with self._cond:
            while self._next not in self._items and self._error is None:
                self._cond.wait()
            if self._next not in self._items:
                raise self._error
            item = self._items.pop(self._next)
            self._next += 1
            self._cond.notify_all()
            return item

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


def _to_image(frame: av.VideoFrame, size: tuple[int, int] | None) -> Image.Image:
    if size is None:
        image = frame.to_image()
    else:
        width, height = size
        image = frame.reformat(width=width, height=height, format="rgb24").to_image()
    image.info["time"] = frame.time
    return image
