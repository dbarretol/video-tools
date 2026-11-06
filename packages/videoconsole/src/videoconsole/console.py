"""Where a command line writes: results to stdout, everything else to stderr.

Text is printed literally (no rich markup or emoji codes), so file names such as
``clip [1080p].mp4`` show as they are, and long lines are not wrapped, so the
output can be searched and redirected.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO

from rich.console import Console as RichConsole
from rich.logging import RichHandler
from rich.text import Text

from videocore import configure_logging

STYLES = {
    "ok": "green",
    "warning": "yellow",
    "error": "red",
    "dim": "dim",
}


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
    where to find more. Streams default to ``sys.stdout``/``sys.stderr`` at the
    time of each write.
    """

    def __init__(
        self, *, quiet: bool = False, stdout: IO[str] | None = None, stderr: IO[str] | None = None
    ) -> None:
        self.quiet = quiet
        self.out = _rich_console(stdout, stderr=False)
        self.err = _rich_console(stderr, stderr=True)

    @property
    def live(self) -> bool:
        """Whether live progress can be drawn: stderr is a terminal and not quiet."""
        return not self.quiet and self.err.is_terminal

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

    Messages go to the same console as the live progress, so they print above
    it instead of breaking it. Returns the log file.
    """
    log_file = configure_logging(app, level=logging.DEBUG if verbose else logging.INFO)
    if verbose:
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
