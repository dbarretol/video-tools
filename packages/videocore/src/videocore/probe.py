"""Container and stream metadata, read in-process with PyAV."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import av

from videocore.errors import VideoOpenError


@dataclass(frozen=True)
class VideoStreamInfo:
    codec: str
    codec_long_name: str
    profile: str | None
    width: int
    height: int
    fps: float
    pixel_format: str | None
    frame_count: int
    """Exact when the container stores it, otherwise estimated from duration and fps."""
    bit_rate: int | None

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True)
class AudioStreamInfo:
    codec: str
    codec_long_name: str
    sample_rate: int
    channels: int
    bit_rate: int | None


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    size_bytes: int
    duration: float
    """Duration in seconds."""
    format_name: str
    format_long_name: str
    bit_rate: int | None
    video: VideoStreamInfo | None
    audio: AudioStreamInfo | None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def duration_hms(self) -> str:
        return format_hms(self.duration)


def format_hms(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def probe(path: str | os.PathLike[str]) -> VideoInfo:
    """Read container, video and audio metadata without decoding any frame."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    try:
        container = av.open(str(path))
    except av.FFmpegError as exc:
        raise VideoOpenError(f"Could not open video: {path}") from exc

    with container:
        duration = _container_duration(container)
        video_stream = container.streams.video[0] if container.streams.video else None
        audio_stream = container.streams.audio[0] if container.streams.audio else None
        return VideoInfo(
            path=path,
            size_bytes=path.stat().st_size,
            duration=duration,
            format_name=container.format.name,
            format_long_name=container.format.long_name,
            bit_rate=container.bit_rate or None,
            video=_video_info(video_stream, duration) if video_stream else None,
            audio=_audio_info(audio_stream) if audio_stream else None,
            tags=dict(container.metadata),
        )


KEYFRAME_SCAN_SECONDS = 60.0
"""How much of the start of a video `keyframe_interval` reads at most."""
KEYFRAME_SCAN_COUNT = 4


def keyframe_interval(path: str | os.PathLike[str]) -> float | None:
    """Seconds between keyframes, from the packets of the first minute; nothing is decoded.

    The largest gap among the first few keyframes, so a stray keyframe right after
    the first one does not hide a long interval. When the scan stops before finding
    them all (the first minute, or the video, ended), the interval it stopped in
    counts for what was read of it.
    None when the file cannot be read or has no video. Reads well under a few MB.
    """
    try:
        with av.open(os.fspath(path)) as container:
            if not container.streams.video:
                return None
            stream = container.streams.video[0]
            keys: list[float] = []
            last = None
            for packet in container.demux(stream):
                if packet.pts is None or packet.time_base is None:
                    continue
                last = float(packet.pts * packet.time_base)
                if packet.is_keyframe:
                    keys.append(last)
                    if len(keys) == KEYFRAME_SCAN_COUNT:
                        break
                if keys and last - keys[0] > KEYFRAME_SCAN_SECONDS:
                    break
    except av.FFmpegError:
        return None
    if not keys:
        return None
    gaps = [b - a for a, b in zip(keys, keys[1:], strict=False)]
    if len(keys) < KEYFRAME_SCAN_COUNT:
        gaps.append(last - keys[-1])  # stopped inside an interval: at least this long
    interval = max(gaps)
    return interval if interval > 0 else None


def _container_duration(container: av.container.InputContainer) -> float:
    if container.duration is not None:
        return container.duration / av.time_base
    for stream in container.streams:
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
    raise VideoOpenError(f"Could not determine duration of {container.name}")


def _video_info(stream: av.VideoStream, duration: float) -> VideoStreamInfo:
    ctx = stream.codec_context
    rate = stream.average_rate or stream.guessed_rate
    fps = float(rate) if rate else 0.0
    return VideoStreamInfo(
        codec=ctx.name,
        codec_long_name=ctx.codec.long_name,
        profile=ctx.profile,
        width=stream.width,
        height=stream.height,
        fps=fps,
        pixel_format=ctx.pix_fmt,
        frame_count=stream.frames or round(duration * fps),
        bit_rate=ctx.bit_rate or None,
    )


def _audio_info(stream: av.AudioStream) -> AudioStreamInfo:
    ctx = stream.codec_context
    return AudioStreamInfo(
        codec=ctx.name,
        codec_long_name=ctx.codec.long_name,
        sample_rate=ctx.sample_rate,
        channels=ctx.layout.nb_channels,
        bit_rate=ctx.bit_rate or None,
    )
