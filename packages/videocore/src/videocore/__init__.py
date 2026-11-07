"""Shared video primitives for video-tools."""

from videocore.batch import BatchResult, default_workers, find_videos, run_parallel
from videocore.errors import (
    EXIT_DEGRADED,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    NoFramesError,
    NoVideoStreamError,
    UsageError,
    VideoOpenError,
    hint_for,
)
from videocore.frames import evenly_spaced, iter_frames, sample_frames
from videocore.grid import compose_grid
from videocore.inputs import (
    Inputs,
    InvalidInput,
    collect_inputs,
    expand_path,
    read_input_list,
)
from videocore.jobs import (
    Footage,
    ItemResult,
    Plan,
    PlannedItem,
    RunLists,
    RunOptions,
    RunReport,
    Status,
    Task,
    default_runs_dir,
    execute,
    plan,
    run_task,
    write_run_lists,
)
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
from videocore.progress import Cancelled, CancelToken, ProgressEvent
from videocore.state import State

__all__ = [
    "Inputs",
    "InvalidInput",
    "collect_inputs",
    "expand_path",
    "read_input_list",
    "EXIT_DEGRADED",
    "EXIT_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_USAGE",
    "AudioStreamInfo",
    "BatchResult",
    "CancelToken",
    "Cancelled",
    "Footage",
    "ItemResult",
    "NoFramesError",
    "NoVideoStreamError",
    "OutputCollisionError",
    "OutputSlot",
    "Plan",
    "PlannedItem",
    "ProgressEvent",
    "RunLists",
    "RunOptions",
    "RunReport",
    "State",
    "Status",
    "Task",
    "UsageError",
    "VideoInfo",
    "VideoOpenError",
    "VideoStreamInfo",
    "atomic_write",
    "compose_grid",
    "configure_logging",
    "default_log_dir",
    "default_runs_dir",
    "default_workers",
    "evenly_spaced",
    "execute",
    "find_videos",
    "format_hms",
    "hint_for",
    "iter_frames",
    "output_folder",
    "part_path",
    "plan",
    "plan_outputs",
    "probe",
    "remove_partials",
    "run_parallel",
    "run_task",
    "sample_frames",
    "write_run_lists",
]


def __getattr__(name: str) -> str:
    # The version is read from the installed metadata only when asked for:
    # importlib.metadata takes longer to import than most commands take to start.
    if name == "__version__":
        from importlib.metadata import version

        return version("videocore")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
