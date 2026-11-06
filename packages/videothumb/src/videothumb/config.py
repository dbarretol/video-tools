"""Defaults of the command line, read from a YAML file (see ``videocore.config``).

One section per mode, plus ``run`` for how folders are processed. The desktop
app does not read this file.
"""

from __future__ import annotations

from videoconsole import ConsoleOptions
from videocore import RunOptions
from videocore.config import AppConfig, Section
from videothumb.common import APP_NAME, Mode
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

RUN_SECTION = "run"
CONSOLE_SECTION = "console"

CONFIG = AppConfig(
    APP_NAME,
    (
        Section(Mode.SHEET.value, (SheetOptions,), "videothumb sheet"),
        Section(Mode.FRAMES.value, (SnapshotOptions,), "videothumb frames"),
        # The output depends on --video or --folder, so it is not a default.
        Section(RUN_SECTION, (RunOptions,), "--folder", skip=("output",)),
        Section(CONSOLE_SECTION, (ConsoleOptions,), "console window and notifications"),
    ),
    f"{APP_NAME} defaults for the command line (the desktop app does not use them).",
)
