"""Shared video primitives for video-tools."""

from importlib.metadata import version

from videocore.batch import BatchResult, default_workers, find_videos, run_parallel
from videocore.errors import VideoOpenError
from videocore.frames import evenly_spaced, iter_frames, sample_frames
from videocore.grid import compose_grid
from videocore.logging_setup import configure_logging, default_log_dir
from videocore.outputs import (
    OutputCollisionError,
    OutputSlot,
    atomic_write,
    output_folder,
    part_path,
    plan_outputs,
    remove_partials,
)
from videocore.probe import AudioStreamInfo, VideoInfo, VideoStreamInfo, format_hms, probe

__version__ = version("videocore")

__all__ = [
    "AudioStreamInfo",
    "BatchResult",
    "OutputCollisionError",
    "OutputSlot",
    "VideoInfo",
    "VideoOpenError",
    "VideoStreamInfo",
    "atomic_write",
    "compose_grid",
    "configure_logging",
    "default_log_dir",
    "default_workers",
    "evenly_spaced",
    "find_videos",
    "format_hms",
    "iter_frames",
    "output_folder",
    "part_path",
    "plan_outputs",
    "probe",
    "remove_partials",
    "run_parallel",
    "sample_frames",
]
