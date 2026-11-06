"""The videos a command works on: paths, globs, folders and lists of videos in text files.

`collect_inputs` joins every source into one list without duplicates, in the
order they were given:

- ``PATH`` arguments are used as they are when they exist, so names with
  brackets such as ``clip [1080p].mp4`` work; only a path that does not exist
  and contains ``*``, ``?`` or ``[`` is expanded as a pattern (the Windows
  shell does not expand them). A path that is neither is a usage error.
- Folders give their videos (subfolders too with `recursive`); files are used
  whatever their extension.
- Input lists (`read_input_list`) have one path per line. Their paths that do
  not exist do not stop the run: they are reported as failed videos.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from videocore.batch import find_videos
from videocore.errors import LONG_PATH_HINT, UsageError, too_long

logger = logging.getLogger(__name__)

PATTERN_CHARS = frozenset("*?[")
STDIN = "-"
"""The input list name that reads standard input."""
LIST_SUFFIXES = frozenset({".txt", ".lst"})
"""Extensions of files that are probably lists of videos rather than videos."""
NOT_FOUND = "not found"
REPLACEMENT_CHARACTER = chr(0xFFFD)
"""What a decoder writes for bytes it cannot read, e.g. names garbled by a pipe."""


@dataclass(frozen=True)
class InvalidInput:
    """A path from an input list that cannot be processed."""

    path: Path
    reason: str


@dataclass
class Inputs:
    videos: list[Path] = field(default_factory=list)
    roots: list[Path | None] = field(default_factory=list)
    """For each video, the folder it was found in (its subfolders are recreated
    inside an output folder), or None for a file given by itself."""
    invalid: list[InvalidInput] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """Things the user should know, e.g. a list that looks badly encoded."""
    single_file: bool = False
    """Exactly one path was given, and it is a file: the command works on one video."""

    @property
    def source(self) -> Path | None:
        """The folder every video was found in, when there is exactly one."""
        roots = {root for root in self.roots}
        return roots.pop() if len(roots) == 1 else None

    def __len__(self) -> int:
        return len(self.videos) + len(self.invalid)


def expand_path(text: str | os.PathLike[str]) -> list[Path]:
    """`text` itself when it exists, else the paths matching it as a pattern (sorted).

    Raises UsageError when it does not exist and matches nothing.
    """
    path = Path(text)
    if path.exists():
        return [path]
    if PATTERN_CHARS.intersection(str(text)):
        matches = sorted(Path(match) for match in glob.glob(str(text), recursive=True))
        if matches:
            return matches
        raise UsageError(f"No files match {text}")
    hint = ""
    if too_long(path):
        hint = f"; {LONG_PATH_HINT}"
    elif path.suffix.lower() in LIST_SUFFIXES:
        hint = " (to read a list of videos from a text file, use --input-list)"
    raise UsageError(f"Not found: {text}{hint}")


def read_input_list(
    source: str | os.PathLike[str], *, stdin: TextIO | None = None
) -> tuple[list[Path], list[str]]:
    """The paths listed in a text file, or in standard input for ``-``; and warnings.

    One path per line; empty lines and lines starting with ``#`` are skipped,
    and spaces and quotes around a path are removed (the Explorer's "Copy as
    path" adds quotes). The file is UTF-8, with or without BOM. Relative paths
    are relative to the list's folder, or to the current folder for ``-``.
    Raises UsageError when the file cannot be read.
    """
    if str(source) == STDIN:
        text = (stdin or sys.stdin).read()
        base, name = Path.cwd(), "standard input"
    else:
        path = Path(source)
        if not path.is_file():
            raise UsageError(f"Input list not found: {path}")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UsageError(f"Input list {path} is not UTF-8 text; save it as UTF-8") from exc
        except OSError as exc:
            raise UsageError(f"Could not read input list {path}: {exc}") from exc
        base, name = path.parent, str(path)
    warnings = []
    if REPLACEMENT_CHARACTER in text:
        warnings.append(
            f"the list in {name} contains replacement characters (U+FFFD): some names were "
            "garbled before reaching it; in PowerShell, write the list to a UTF-8 file and use "
            "--input-list FILE instead of a pipe"
        )
    paths = []
    for line in text.splitlines():
        entry = line.strip().strip("\"'").strip()
        if not entry or entry.startswith("#"):
            continue
        path = Path(entry)
        paths.append(path if path.is_absolute() else base / path)
    return paths, warnings


def collect_inputs(
    paths: Iterable[str | os.PathLike[str]] = (),
    input_lists: Sequence[str | os.PathLike[str]] = (),
    *,
    recursive: bool = False,
    skip_dir: Callable[[Path], bool] | None = None,
    stdin: TextIO | None = None,
) -> Inputs:
    """Every video given by `paths` and `input_lists`, once each, in order.

    Raises UsageError for a path argument that does not exist (see
    `expand_path`) and for an input list that cannot be read.
    """
    candidates: list[tuple[Path, bool]] = []  # (path, from a list)
    paths = list(paths)
    for text in paths:
        candidates += [(path, False) for path in expand_path(text)]
    inputs = Inputs()
    for source in input_lists:
        listed, warnings = read_input_list(source, stdin=stdin)
        candidates += [(path, True) for path in listed]
        inputs.warnings += warnings

    seen: set[str] = set()

    def add(video: Path, root: Path | None) -> None:
        key = os.path.normcase(os.path.abspath(video))
        if key not in seen:
            seen.add(key)
            inputs.videos.append(video)
            inputs.roots.append(root)

    for path, _listed in candidates:
        if path.is_dir():
            for video in find_videos(path, recursive=recursive, skip_dir=skip_dir):
                add(video, path)
        elif path.is_file():
            add(path, None)
        else:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                inputs.invalid.append(InvalidInput(path, NOT_FOUND))
    inputs.single_file = not input_lists and len(candidates) == 1 and candidates[0][0].is_file()
    if inputs.invalid:
        logger.warning("%d listed paths were not found", len(inputs.invalid))
    return inputs


def add_input_arguments(parser: argparse.ArgumentParser, what: str = "videos or folders") -> None:
    """``PATH...`` and ``--input-list FILE`` (repeatable); check them with `require_inputs`."""
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help=f"{what}; patterns such as *.mp4 are expanded when no file has that name",
    )
    parser.add_argument(
        "--input-list",
        action="append",
        default=[],
        metavar="FILE",
        help="text file with one path per line (- reads standard input); can be repeated",
    )


def require_inputs(args: argparse.Namespace) -> None:
    """Raise UsageError when neither ``PATH`` nor ``--input-list`` was given."""
    if not args.paths and not args.input_list:
        raise UsageError("Give at least one PATH or --input-list FILE")
