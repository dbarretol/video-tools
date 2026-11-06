"""Exceptions raised by videocore, and what to tell the user about them."""

from __future__ import annotations

import errno

_CLOUD_HINT = (
    "the file is only in OneDrive (or another cloud folder) and could not be downloaded; "
    "check that OneDrive is running and online, or choose 'Always keep on this device'"
)


class VideoOpenError(OSError):
    """The file could not be opened or decoded as a video."""

    hint = (
        "the file may be damaged, still being downloaded or copied, or in a format that cannot "
        "be read; check that it plays in a video player"
    )


class NoVideoStreamError(VideoOpenError, ValueError):
    """The file opens but has no video stream (e.g. an audio file)."""

    hint = "it has no video to take frames from; check that it is a video and not an audio file"


class NoFramesError(RuntimeError):
    """Not a single frame could be decoded where frames were needed."""

    hint = (
        "the video may be damaged or use a codec that cannot be decoded; check that it plays, "
        "or convert it (e.g. with HandBrake) and try again"
    )


class UsageError(ValueError):
    """The command was given invalid arguments, inputs or settings (exit code 2)."""


# Exit codes shared by every command-line tool.
EXIT_OK = 0
EXIT_FAILED = 1
"""At least one video failed (or, for `verify`, the video does not match)."""
EXIT_USAGE = 2
"""Invalid arguments, inputs or config; nothing was processed."""
EXIT_DEGRADED = 3
"""No video failed, but some outputs are incomplete (DEGRADED)."""
EXIT_INTERRUPTED = 130
"""Stopped with Ctrl+C (128 + SIGINT, the shell convention)."""

# Windows errors of cloud files (ERROR_CLOUD_FILE_*), e.g. 362: the provider is not running.
_CLOUD_WINERRORS = range(358, 405)
_DISK_FULL_WINERRORS = (39, 112)  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL


def hint_for(exc: BaseException, *, offline: bool = False) -> str | None:
    """A short suggestion of what to do about `exc`, or None when there is none.

    The exception and its causes are looked at in turn; an exception class can
    give its own advice with a ``hint`` attribute. `offline` says the file was
    only in the cloud, which explains most read errors.
    """
    if offline and isinstance(exc, OSError):
        return _CLOUD_HINT
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if hint := _hint_of(current):
            return hint
        current = current.__cause__ or current.__context__
    return None


def _hint_of(exc: BaseException) -> str | None:
    hint = getattr(type(exc), "hint", None)
    if isinstance(hint, str):
        return hint
    if _is_av_error(exc, "DecoderNotFoundError"):
        return "the video codec is not supported; convert the video (e.g. with HandBrake)"
    if _is_av_error(exc, "InvalidDataError"):
        return VideoOpenError.hint
    if not isinstance(exc, OSError):
        return None
    winerror = getattr(exc, "winerror", None)
    if winerror in _CLOUD_WINERRORS:
        return _CLOUD_HINT
    if exc.errno == errno.ENOSPC or winerror in _DISK_FULL_WINERRORS:
        return "the disk is full; free some space or choose another output folder with -o"
    if isinstance(exc, FileNotFoundError):
        return "the file or folder was moved, renamed or deleted while the run was going on"
    if isinstance(exc, PermissionError):
        return (
            "access was denied; close programs that may have the file open, and check that "
            "the output folder can be written"
        )
    return None


def _is_av_error(exc: BaseException, name: str) -> bool:
    try:
        import av.error
    except ImportError:  # pragma: no cover - av is a dependency
        return False
    cls = getattr(av.error, name, None)
    return cls is not None and isinstance(exc, cls)
