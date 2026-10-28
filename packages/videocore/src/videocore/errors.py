"""Exceptions raised by videocore."""

from __future__ import annotations


class VideoOpenError(OSError):
    """The file could not be opened or decoded as a video."""
