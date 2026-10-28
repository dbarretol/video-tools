"""User preferences remembered between sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from videocore import default_log_dir
from videothumb.common import APP_NAME


@dataclass
class AppSettings:
    mode: str = "sheet"
    columns: int = 5
    rows: int = 4
    thumbnail_width: int = 320
    snapshot_count: int = 10
    jpeg_quality: int = 90
    precise: bool = False


def default_settings_path() -> Path:
    """Next to the log folder: %LOCALAPPDATA%/videothumb/settings.json on Windows."""
    return default_log_dir(APP_NAME).parent / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    """Saved preferences, or defaults when the file is missing or invalid."""
    path = path or default_settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    defaults = AppSettings()
    # Keep known keys with the expected type; anything else falls back to the default.
    values = {
        f.name: raw[f.name]
        for f in fields(AppSettings)
        if f.name in raw and type(raw[f.name]) is type(getattr(defaults, f.name))
    }
    return AppSettings(**values)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
