"""What a tool remembers between runs: ``state.json``, not meant to be edited by hand.

It lives next to the logs (``%LOCALAPPDATA%/<app>/state.json`` on Windows) and
holds things such as how fast each task ran on this computer, used to estimate
the duration of the next runs, and in the future what a GUI last used. Losing
it only loses those memories, so a missing or damaged file is read as empty and
failures to write it are logged and ignored.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from videocore.logging_setup import default_log_dir
from videocore.outputs import atomic_write

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
SPEED_KEY = "speed"
SPEED_LIMITS = (0.05, 20.0)
SPEED_MEMORY = 0.5
"""Weight of the previous speed when a new measure arrives (0: forget it at once)."""


def default_state_path(app: str) -> Path:
    """%LOCALAPPDATA%/<app>/state.json on Windows, $XDG_STATE_HOME/<app>/state.json elsewhere."""
    return default_log_dir(app).parent / STATE_FILE


class State:
    """The state of one tool, read when created; `save` writes it back."""

    def __init__(self, app: str, path: Path | None = None) -> None:
        self.path = path or default_state_path(app)
        self.data: dict[str, Any] = _read(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Change `key` in memory; `value` must be JSON-serializable."""
        self.data[key] = value

    def save(self) -> None:
        try:
            with atomic_write(self.path) as part:
                part.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError:
            logger.debug("Could not save %s", self.path, exc_info=True)

    def speed(self, task: str) -> float:
        """How many times longer `task` takes here than its built-in estimate; 1.0 if unknown."""
        speeds = self.data.get(SPEED_KEY)
        value = speeds.get(task) if isinstance(speeds, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 1.0
        low, high = SPEED_LIMITS
        return min(max(float(value), low), high)

    def learn_speed(self, task: str, measured: float | None) -> None:
        """Blend a new measure (see ``RunReport.measured_speed``) into `task`'s speed and save."""
        if measured is None or measured <= 0:
            return
        speeds = self.data.get(SPEED_KEY)
        if not isinstance(speeds, dict):
            speeds = self.data[SPEED_KEY] = {}
        known = task in speeds
        blended = SPEED_MEMORY * self.speed(task) + (1 - SPEED_MEMORY) * measured
        low, high = SPEED_LIMITS
        speeds[task] = round(min(max(blended if known else measured, low), high), 3)
        self.save()


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.debug("Ignoring unreadable %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}
