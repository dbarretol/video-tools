"""Shared video primitives for video-tools."""

from importlib.metadata import version

from videocore.batch import BatchResult, default_workers, find_videos, run_parallel
from videocore.errors import VideoOpenError
from videocore.frames import evenly_spaced, iter_frames, sample_frames
from videocore.grid import compose_grid
from videocore.logging_setup import configure_logging, default_log_dir
from videocore.probe import AudioStreamInfo, VideoInfo, VideoStreamInfo, format_hms, probe

__version__ = version("videocore")

__all__ = [
    "AudioStreamInfo",
    "BatchResult",
    "VideoInfo",
    "VideoOpenError",
    "VideoStreamInfo",
    "compose_grid",
    "configure_logging",
    "default_log_dir",
    "default_workers",
    "evenly_spaced",
    "find_videos",
    "format_hms",
    "iter_frames",
    "probe",
    "run_parallel",
    "sample_frames",
]
