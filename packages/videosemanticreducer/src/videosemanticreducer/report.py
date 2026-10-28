"""Plain-text metadata report (``<stem>_info.txt``)."""

from __future__ import annotations

from pathlib import Path

from videocore import VideoInfo


def format_size(size_bytes: float) -> str:
    """Human-readable size with binary units, e.g. ``301.03 MB``."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def format_report(info: VideoInfo) -> str:
    lines = [
        "=== FILE ===",
        f"Name: {info.filename}",
        f"Path: {info.path.resolve()}",
        f"Size: {format_size(info.size_bytes)} ({info.size_bytes} bytes)",
        f"Duration: {info.duration_hms} ({info.duration:.3f} s)",
        f"Format: {info.format_long_name} ({info.format_name})",
    ]
    if info.bit_rate:
        lines.append(f"Overall bit rate: {info.bit_rate // 1000} kb/s")

    lines += ["", "=== VIDEO ==="]
    if info.video is not None:
        video = info.video
        lines += [
            f"Codec: {video.codec_long_name} ({video.codec})",
            f"Resolution: {video.resolution}",
            f"Frame rate: {video.fps:.2f} fps",
            f"Pixel format: {video.pixel_format}",
            f"Frames: {video.frame_count}",
        ]
        if video.bit_rate:
            lines.append(f"Bit rate: {video.bit_rate // 1000} kb/s")
    else:
        lines.append("No video stream.")

    lines += ["", "=== AUDIO ==="]
    if info.audio is not None:
        audio = info.audio
        lines += [
            f"Codec: {audio.codec_long_name} ({audio.codec})",
            f"Sample rate: {audio.sample_rate} Hz",
            f"Channels: {audio.channels}",
        ]
        if audio.bit_rate:
            lines.append(f"Bit rate: {audio.bit_rate // 1000} kb/s")
    else:
        lines.append("No audio stream.")

    lines += ["", "=== TAGS ==="]
    if info.tags:
        lines += [f"{key}: {value}" for key, value in info.tags.items()]
    else:
        lines.append("No embedded tags.")
    return "\n".join(lines) + "\n"


def write_report(info: VideoInfo, path: Path) -> Path:
    path.write_text(format_report(info), encoding="utf-8")
    return path
