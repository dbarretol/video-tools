"""Exceptions raised by videocore."""

from __future__ import annotations


class VideoOpenError(OSError):
    """The file could not be opened or decoded as a video."""


class UsageError(ValueError):
    """The command was given invalid arguments, inputs or settings (exit code 2)."""


# Exit codes shared by every command-line tool.
EXIT_OK = 0
EXIT_FAILED = 1
"""At least one video failed (or, for `verify`, the video does not match)."""
EXIT_USAGE = 2
"""Invalid arguments, inputs or config; nothing was processed."""
EXIT_INTERRUPTED = 130
"""Stopped with Ctrl+C (128 + SIGINT, the shell convention)."""
