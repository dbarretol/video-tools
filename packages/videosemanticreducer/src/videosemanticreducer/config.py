"""User defaults for the ``process`` command, read from a YAML file.

Values are looked up in this order: command-line flags, then the config file,
then the built-in defaults below. The config file is, in order of preference:

1. the path given with ``--config``;
2. the path in the ``VIDEOSEMANTICREDUCER_CONFIG`` environment variable;
3. ``config.yaml`` in the user config folder (see `default_config_path`), if it exists.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from videocore import UsageError
from videocore.options import option, options_of, parse_mapping, validate
from videosemanticreducer.pipeline import ProcessOptions

APP_NAME = "videosemanticreducer"
CONFIG_ENV = "VIDEOSEMANTICREDUCER_CONFIG"
SECTION = "process"


@dataclass(frozen=True)
class RunDefaults:
    """How to run `process`; unlike `ProcessOptions`, these do not change the archive."""

    output: Path | None = option(
        None,
        "output folder; with -r, subfolders are recreated inside it "
        "(in the config file, relative to the file)",
        flags=("-o", "--output"),
        default_text="a <name>_summary folder next to each video",
    )
    workers: int | None = option(
        None,
        "videos processed in parallel for folders",
        flags=("-w", "--workers"),
        gt=0,
        default_text="automatic",
        advanced=True,
    )
    recursive: bool = option(
        False, "include subfolders when processing a folder", flags=("-r", "--recursive")
    )

    def __post_init__(self) -> None:
        validate(self)


@dataclass(frozen=True)
class ProcessDefaults:
    """The defaults of `process`: built-in ones, changed by the config file."""

    options: ProcessOptions = field(default_factory=ProcessOptions)
    run: RunDefaults = field(default_factory=RunDefaults)


class ConfigError(UsageError):
    """The config file is missing, unreadable or has invalid values."""


def default_config_path() -> Path:
    """%APPDATA%/videosemanticreducer/config.yaml on Windows, $XDG_CONFIG_HOME elsewhere."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME / "config.yaml"


def find_config(explicit: Path | None = None) -> Path | None:
    """The config file to use, or None when there is none. Explicit paths must exist."""
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        return explicit
    if env := os.environ.get(CONFIG_ENV):
        path = Path(env)
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path} (from {CONFIG_ENV})")
        return path
    path = default_config_path()
    return path if path.is_file() else None


def load_config(path: Path | None) -> ProcessDefaults:
    """Read defaults from `path`; built-in defaults when `path` is None."""
    if path is None:
        return ProcessDefaults()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    if data is None:
        return ProcessDefaults()
    if not isinstance(data, dict) or set(data) - {SECTION}:
        raise ConfigError(f"{path}: expected a single '{SECTION}:' section")
    section = data.get(SECTION) or {}
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: '{SECTION}' must be a mapping of option: value")
    try:
        values = parse_mapping([ProcessOptions, RunDefaults], section)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from None

    if values.get("output") is not None and not values["output"].is_absolute():
        values["output"] = path.parent / values["output"]
    return split_values(values)


def split_values(values: dict, base: ProcessDefaults | None = None) -> ProcessDefaults:
    """`base` (built-in defaults by default) with `values` applied, by field name."""
    base = base or ProcessDefaults()

    def pick(cls: type) -> dict:
        return {opt.name: values[opt.name] for opt in options_of(cls) if opt.name in values}

    return ProcessDefaults(
        options=dataclasses.replace(base.options, **pick(ProcessOptions)),
        run=dataclasses.replace(base.run, **pick(RunDefaults)),
    )


def render_template(defaults: ProcessDefaults | None = None) -> str:
    """A commented YAML config with every option set to `defaults` (built-in by default)."""
    defaults = defaults or ProcessDefaults()
    lines = [
        "# videosemanticreducer defaults for the `process` command.",
        "# Command-line flags override these values; delete a line to use the built-in default.",
        f"{SECTION}:",
    ]
    for settings in (defaults.options, defaults.run):
        for opt in options_of(type(settings)):
            value = getattr(settings, opt.name)
            rendered = yaml.safe_dump(
                str(value) if isinstance(value, Path) else value, default_flow_style=True
            )
            rendered = rendered.strip().removesuffix("...").strip()
            comment = opt.spec.help
            if opt.optional and opt.spec.default_text:
                comment += f"; null = {opt.spec.default_text}"
            lines.append(f"  {opt.name}: {rendered}  # {comment}")
    return "\n".join(lines) + "\n"
