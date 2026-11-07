"""Where a command line writes: results to stdout, everything else to stderr.

Text is printed literally (no rich markup or emoji codes), so file names such as
``clip [1080p].mp4`` show as they are, and long lines are not wrapped, so the
output can be searched and redirected.
"""

from __future__ import annotations

import json as json_module
import logging
import sys
from pathlib import Path
from typing import IO, Any

from rich.console import Console as RichConsole
from rich.logging import RichHandler
from rich.text import Text

from videocore import configure_logging
from videocore.logging_setup import for_the_user

STYLES = {
    "ok": "green",
    "warning": "yellow",
    "error": "red",
    "dim": "dim",
}


JSON_SCHEMA = 1
"""Version of the ``--json`` document; raised when a field changes meaning or is removed."""


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _rich_console(file: IO[str] | None, stderr: bool) -> RichConsole:
    return RichConsole(
        file=file,
        stderr=stderr,
        markup=False,
        emoji=False,
        highlight=False,
        soft_wrap=True,
    )


class Console:
    """The output of one command-line run.

    `quiet` (``-q``) leaves only what needs attention: warnings, errors and
    where to find more. With `json` (``--json``), stdout only receives one JSON
    document, written by `finish`, and every message goes to stderr; the
    command adds to the document with `record`. Without `progress`
    (``--no-progress``) no live progress is drawn. Streams default to
    ``sys.stdout``/``sys.stderr`` at the time of each write.
    """

    def __init__(
        self,
        *,
        quiet: bool = False,
        json: bool = False,
        tool: str | None = None,
        progress: bool = True,
        stdout: IO[str] | None = None,
        stderr: IO[str] | None = None,
    ) -> None:
        self.quiet = quiet
        self.json = json
        self.progress = progress
        self.document: dict[str, Any] = {"schema": JSON_SCHEMA, "tool": tool}
        self._stdout = stdout
        self.out = _rich_console(stderr if json else stdout, stderr=json)
        self.err = _rich_console(stderr, stderr=True)

    def record(self, **fields: Any) -> None:
        """Add `fields` to the JSON document (ignored without `json`)."""
        self.document.update(fields)

    def record_error(self, exc: BaseException, hint: str | None = None) -> None:
        """Record the error that stopped the command."""
        self.record(error={"type": type(exc).__name__, "message": str(exc), "hint": hint})

    def finish(self, exit_code: int, *, log_file: Path | None = None) -> int:
        """Write the JSON document, with `json`; return `exit_code`.

        The document is ASCII (other characters escaped), so it reads the same
        whatever the encoding of the console or pipe.
        """
        if self.json:
            document = {**self.document, "exit_code": exit_code, "log_file": log_file}
            text = json_module.dumps(document, ensure_ascii=True, indent=2, default=_jsonable)
            stream = self._stdout or sys.stdout
            stream.write(text + "\n")
            stream.flush()
        return exit_code

    @property
    def live(self) -> bool:
        """Whether live progress is drawn: stderr is a terminal, not quiet nor --no-progress."""
        return self.progress and not self.quiet and self.err.is_terminal

    def info(self, text: str, style: str | None = None) -> None:
        """A normal message to stdout; omitted with `quiet`."""
        if not self.quiet:
            self.out.print(Text(text, style=STYLES.get(style or "", style or "")))

    def notice(self, text: str) -> None:
        """Something to act on even in quiet mode (e.g. a list of videos to retry)."""
        self.out.print(Text(text))

    def warning(self, text: str) -> None:
        self.err.print(Text(f"Warning: {text}", style=STYLES["warning"]))

    def error(self, text: str) -> None:
        self.err.print(Text(f"Error: {text}", style=STYLES["error"]))

    def detail(self, text: str, style: str | None = None) -> None:
        """A line to stderr that explains a warning or error (hints, log paths)."""
        self.err.print(Text(text, style=STYLES.get(style or "", style or "")))

    def hint(self, hint: str | None, log_file: Path | None) -> None:
        """The hint of a failure, if any, and where its details are."""
        if hint:
            self.detail(f"  Hint: {hint}")
        if log_file is not None:
            self.detail(f"Details: {log_file}", "dim")


def setup_logging(app: str, console: Console, *, verbose: bool) -> Path:
    """Log to the app's file; with `verbose`, show debug messages on stderr too.

    Without it, warnings logged with ``extra=FOR_THE_USER`` (see
    ``videocore.logging_setup``) are shown as console warnings. Messages go to
    the same console as the live progress, so they print above it instead of
    breaking it. Returns the log file.
    """
    log_file = configure_logging(app, level=logging.DEBUG if verbose else logging.INFO)
    if not verbose:
        handler = _UserWarnings(console)
        handler._videocore = True  # type: ignore[attr-defined] - replaced by the next setup
        logging.getLogger().addHandler(handler)
    else:
        handler = RichHandler(
            level=logging.DEBUG,
            console=console.err,
            show_time=False,
            show_path=False,
            markup=False,
        )
        handler._videocore = True  # type: ignore[attr-defined] - replaced by the next setup
        logging.getLogger().addHandler(handler)
    return log_file


class _UserWarnings(logging.Handler):
    """Shows the warnings meant for the user as `Console.warning` lines."""

    def __init__(self, console: Console) -> None:
        super().__init__(logging.WARNING)
        self.console = console
        self.addFilter(for_the_user)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.console.warning(record.getMessage())
        except Exception:  # noqa: BLE001 - logging must never break the run
            self.handleError(record)
