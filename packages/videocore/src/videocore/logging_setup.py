"""Logging configuration shared by the applications."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def default_log_dir(app_name: str) -> Path:
    """%LOCALAPPDATA%/<app>/logs on Windows, $XDG_STATE_HOME/<app>/logs elsewhere."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / app_name / "logs"


def configure_logging(
    app_name: str,
    *,
    level: int = logging.INFO,
    log_dir: Path | None = None,
    console: bool = False,
) -> Path:
    """Send logs to a rotating file (and optionally stderr); return the log file path.

    Safe to call more than once: handlers added by a previous call are replaced.
    """
    log_dir = log_dir or default_log_dir(app_name)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{app_name}.log"

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_videocore", False)]:
        root.removeHandler(handler)
        handler.close()

    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler._videocore = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level)
    return log_file
