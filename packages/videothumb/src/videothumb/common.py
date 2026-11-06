"""Definitions shared by every module."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from videocore.options import option

APP_NAME = "videothumb"

Sampling = Literal["keyframe", "precise"]
"""``keyframe`` is fast but a frame can be up to a few seconds early; ``precise`` is exact."""


class Mode(StrEnum):
    SHEET = "sheet"
    FRAMES = "frames"
    SNAPSHOTS = "frames"
    """Old name of FRAMES, still used by the tkinter app."""

    @classmethod
    def _missing_(cls, value: object) -> Mode | None:
        return cls.FRAMES if value == "snapshots" else None  # settings saved before the rename


def jpeg_quality_option() -> Any:
    """The ``jpeg_quality`` field shared by every mode."""
    return option(90, "JPEG quality", flags=("--quality",), ge=1, le=100, label="JPEG quality")


def sampling_option() -> Any:
    """The ``sampling`` field shared by every mode; the command line sets it with --precise."""
    return option(
        "keyframe",
        "keyframe: fast, a frame can be a few seconds early; precise: exact positions",
        label="Frame positions",
        advanced=True,
        cli=False,
    )
