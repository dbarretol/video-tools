"""User defaults for the ``process`` command, read from a YAML file.

Values are looked up in this order: command-line flags, then the config file,
then the built-in defaults below. The config file is, in order of preference:

1. the path given with ``--config``;
2. the path in the ``VIDEOSEMANTICREDUCER_CONFIG`` environment variable;
3. ``config.yaml`` in the user config folder (see `default_config_path`), if it exists.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

APP_NAME = "videosemanticreducer"
CONFIG_ENV = "VIDEOSEMANTICREDUCER_CONFIG"
SECTION = "process"


@dataclass(frozen=True)
class ProcessDefaults:
    num_snapshots: int = 150
    snap_duration: float = 0.4
    thumb_cols: int = 12
    thumb_rows: int = 12
    summary: bool = True
    thumbnail: bool = True
    fingerprint: bool = True
    output: Path | None = None
    workers: int | None = None
    recursive: bool = False


# What each value means, written as comments in the template.
DESCRIPTIONS = {
    "num_snapshots": "snapshots in the summary video",
    "snap_duration": "seconds each snapshot is shown",
    "thumb_cols": "thumbnail grid columns",
    "thumb_rows": "thumbnail grid rows",
    "summary": "create <stem>_summary.mp4",
    "thumbnail": "create <stem>_thumb.jpg",
    "fingerprint": "create <stem>.fingerprint.json",
    "output": "output folder; null = a <stem>_summary folder next to each video. "
    "Relative paths are relative to this file",
    "workers": "videos processed in parallel for folders; null = automatic",
    "recursive": "include subfolders when processing a folder",
}

_TYPES: dict[str, tuple[type, ...]] = {
    "num_snapshots": (int,),
    "snap_duration": (int, float),
    "thumb_cols": (int,),
    "thumb_rows": (int,),
    "summary": (bool,),
    "thumbnail": (bool,),
    "fingerprint": (bool,),
    "output": (str, type(None)),
    "workers": (int, type(None)),
    "recursive": (bool,),
}


class ConfigError(ValueError):
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

    values: dict[str, Any] = {}
    for key, value in section.items():
        if key not in _TYPES:
            valid = ", ".join(_TYPES)
            raise ConfigError(f"{path}: unknown option '{key}' (valid options: {valid})")
        expected = _TYPES[key]
        # bool is a subclass of int: reject `true` where a number is expected.
        if not isinstance(value, expected) or (isinstance(value, bool) and bool not in expected):
            names = " or ".join("null" if t is type(None) else t.__name__ for t in expected)
            raise ConfigError(f"{path}: '{key}' must be {names}, got {value!r}")
        values[key] = value

    if values.get("output") is not None:
        output = Path(values["output"]).expanduser()
        values["output"] = output if output.is_absolute() else path.parent / output
    if "snap_duration" in values:
        values["snap_duration"] = float(values["snap_duration"])
    return ProcessDefaults(**values)


def render_template(defaults: ProcessDefaults | None = None) -> str:
    """A commented YAML config with every option set to `defaults` (built-in by default)."""
    defaults = defaults or ProcessDefaults()
    lines = [
        "# videosemanticreducer defaults for the `process` command.",
        "# Command-line flags override these values; delete a line to use the built-in default.",
        f"{SECTION}:",
    ]
    for field in fields(ProcessDefaults):
        value = getattr(defaults, field.name)
        rendered = yaml.safe_dump(
            str(value) if isinstance(value, Path) else value, default_flow_style=True
        )
        rendered = rendered.strip().removesuffix("...").strip()
        lines.append(f"  {field.name}: {rendered}  # {DESCRIPTIONS[field.name]}")
    return "\n".join(lines) + "\n"
