"""Defaults of the command line, read from a YAML file (see ``videocore.config``).

One section per mode, plus ``run`` for how folders are processed. The desktop
app does not read this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from videoconsole import ConsoleOptions
from videocore import RunOptions
from videocore.config import AppConfig, Section
from videocore.options import option, validate
from videothumb.common import APP_NAME, Mode
from videothumb.pack import PackOptions
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

RUN_SECTION = "run"
CONSOLE_SECTION = "console"


@dataclass(frozen=True)
class TaskOptions:
    default_task: Literal["sheet", "frames", "pack"] = option(
        "sheet",
        "what `videothumb PATH...` does when no command is given",
        label="Default task",
        cli=False,
    )

    def __post_init__(self) -> None:
        validate(self)


CONFIG = AppConfig(
    APP_NAME,
    (
        Section(Mode.SHEET.value, (SheetOptions,), "videothumb sheet"),
        Section(Mode.FRAMES.value, (SnapshotOptions,), "videothumb frames"),
        Section(Mode.PACK.value, (PackOptions,), "videothumb pack"),
        # The output depends on --video or --folder, so it is not a default.
        Section(RUN_SECTION, (RunOptions,), "--folder", skip=("output",)),
        Section(
            CONSOLE_SECTION,
            (ConsoleOptions, TaskOptions),
            "default task, console window and notifications",
        ),
    ),
    f"{APP_NAME} defaults for the command line (the desktop app does not use them).",
)
