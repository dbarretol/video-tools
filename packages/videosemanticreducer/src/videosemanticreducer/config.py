"""User defaults for the ``archive`` command, read from a YAML file.

Values are looked up in this order: command-line flags, then the config file,
then the built-in defaults. The file is the one given with ``--config``, else
the one in ``$VIDEOSEMANTICREDUCER_CONFIG``, else ``config.yaml`` in the user
config folder if it exists (see ``videocore.config``).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from videoconsole import ConsoleOptions
from videocore import RunOptions
from videocore.config import AppConfig, ConfigError, Section
from videocore.options import option, options_of, validate
from videosemanticreducer.pipeline import ProcessOptions

APP_NAME = "videosemanticreducer"
SECTION = "archive"
CONSOLE_SECTION = "console"


@dataclass(frozen=True)
class TaskOptions:
    # `cleanup`, once it exists, must never be the default: it deletes files.
    default_task: Literal["archive", "fingerprint", "status"] = option(
        "archive",
        "what `videosemanticreducer PATH...` does when no command is given",
        label="Default task",
        cli=False,
    )

    def __post_init__(self) -> None:
        validate(self)


CONFIG = AppConfig(
    APP_NAME,
    (
        Section(SECTION, (ProcessOptions, RunOptions)),
        Section(
            CONSOLE_SECTION,
            (ConsoleOptions, TaskOptions),
            "default task, console window and notifications",
        ),
    ),
    f"{APP_NAME} defaults for the `archive` command.",
)
CONFIG_ENV = CONFIG.env_var

__all__ = [
    "CONFIG",
    "CONFIG_ENV",
    "ConfigError",
    "ProcessDefaults",
    "default_config_path",
    "find_config",
    "load_config",
    "render_template",
    "split_values",
]


@dataclass(frozen=True)
class ProcessDefaults:
    """The defaults of `archive`: built-in ones, changed by the config file."""

    options: ProcessOptions = field(default_factory=ProcessOptions)
    run: RunOptions = field(default_factory=RunOptions)


def default_config_path() -> Path:
    """%APPDATA%/videosemanticreducer/config.yaml on Windows, $XDG_CONFIG_HOME elsewhere."""
    return CONFIG.default_path()


def find_config(explicit: Path | None = None) -> Path | None:
    """The config file to use, or None when there is none. Explicit paths must exist."""
    return CONFIG.find(explicit)


def load_config(path: Path | None) -> ProcessDefaults:
    """Read defaults from `path`; built-in defaults when `path` is None."""
    return split_values(CONFIG.load(path)[SECTION])


def split_values(values: dict, base: ProcessDefaults | None = None) -> ProcessDefaults:
    """`base` (built-in defaults by default) with `values` applied, by field name."""
    base = base or ProcessDefaults()

    def pick(cls: type) -> dict:
        return {opt.name: values[opt.name] for opt in options_of(cls) if opt.name in values}

    return ProcessDefaults(
        options=dataclasses.replace(base.options, **pick(ProcessOptions)),
        run=dataclasses.replace(base.run, **pick(RunOptions)),
    )


def load_console_options(path: Path | None) -> ConsoleOptions:
    """The ``console`` section of `path`; built-in defaults when `path` is None."""
    values = CONFIG.load(path)[CONSOLE_SECTION]
    return ConsoleOptions(**CONFIG.section(CONSOLE_SECTION).pick(ConsoleOptions, values))


def render_template(defaults: ProcessDefaults | None = None) -> str:
    """A commented YAML config with every option set to `defaults` (built-in by default)."""
    defaults = defaults or ProcessDefaults()
    values = {
        opt.name: getattr(settings, opt.name)
        for settings in (defaults.options, defaults.run)
        for opt in options_of(type(settings))
    }
    return CONFIG.render({SECTION: values})
