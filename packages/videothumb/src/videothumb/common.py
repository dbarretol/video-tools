"""Definitions shared by every module."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

APP_NAME = "videothumb"

Sampling = Literal["keyframe", "precise"]
"""``keyframe`` is fast but a frame can be up to a few seconds early; ``precise`` is exact."""


class Mode(StrEnum):
    SHEET = "sheet"
    SNAPSHOTS = "snapshots"
