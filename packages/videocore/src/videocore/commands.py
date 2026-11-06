"""Command-line routing shared by the tools: help, the default task and early config lookups.

``videothumb clip.mp4`` runs ``videothumb sheet clip.mp4``: when the first
argument is not a command, the tool's default task (configurable) is put in
front of the arguments. A command name always wins over a file of that name;
``.\\sheet`` names the file.

``help``, ``help COMMAND``, ``/?``, ``-?`` and ``/help`` ask for the help like
``--help`` does (see `with_help`).
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any

from videocore.config import AppConfig

HELP_FLAGS = frozenset({"-h", "--help", "--version"})
HELP_ALIASES = frozenset({"/?", "-?", "/help"})
"""Other ways of writing ``--help``, as Windows programs accept them."""
HELP_WIDTH = 79
EXAMPLE_COLUMN = 40


def with_help(
    argv: Sequence[str], commands: Collection[str], *, global_flags: Collection[str] = ()
) -> list[str]:
    """`argv` with the other ways of asking for help written as ``--help``.

    ``/?``, ``-?`` and ``/help`` anywhere become ``--help``. ``help`` as the
    command becomes ``--help`` (the help of the tool), and ``help COMMAND``
    becomes ``COMMAND --help``. Flags in `global_flags` may come before it.
    """
    argv = ["--help" if arg in HELP_ALIASES else arg for arg in argv]
    index = 0
    while index < len(argv) and argv[index] in global_flags:
        index += 1
    if index == len(argv) or argv[index] != "help":
        return argv
    rest = argv[index + 1 :]
    command = rest[:1] if rest and rest[0] in commands else []
    return [*argv[:index], *command, "--help"]


def help_epilog(examples: Sequence[tuple[str, str]], *notes: str) -> str:
    """The end of a tool's help: aligned `examples` (command, what it does), then `notes`.

    Already wrapped, for a parser with ``argparse.RawDescriptionHelpFormatter``.
    """
    # A command too long for the column gets its own line, with the text below it.
    fitting = (len(command) for command, _ in examples if len(command) <= EXAMPLE_COLUMN)
    width = max(fitting, default=0)
    indent = " " * (width + 4)
    lines = ["examples:"]
    for command, text in examples:
        first = f"  {command:<{width}}  "
        if len(command) > width:
            lines.append(f"  {command}")
            first = indent
        lines.extend(
            textwrap.wrap(text, HELP_WIDTH, initial_indent=first, subsequent_indent=indent)
        )
    for note in notes:
        lines.extend(["", *textwrap.wrap(note, HELP_WIDTH)])
    return "\n".join(lines)


def with_default_task(
    argv: Sequence[str],
    commands: Collection[str],
    task: Callable[[], str],
    *,
    global_flags: Collection[str] = (),
) -> list[str]:
    """`argv` with ``task()`` inserted when its first argument is not a command.

    Flags in `global_flags` (such as ``-v``) may come before the command and are
    skipped. Without arguments, or with only global flags or help flags, `argv`
    is left as it is, so the help is shown. `task` is only called when needed.
    """
    argv = list(argv)
    index = 0
    while index < len(argv) and argv[index] in global_flags:
        index += 1
    if index == len(argv) or argv[index] in commands or argv[index] in HELP_FLAGS:
        return argv
    return [*argv[:index], task(), *argv[index:]]


def config_argument(argv: Sequence[str]) -> Path | None:
    """The file given with ``--config FILE`` or ``--config=FILE`` in `argv`, if any."""
    for index, arg in enumerate(argv):
        if arg == "--config" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if arg.startswith("--config="):
            return Path(arg.removeprefix("--config="))
    return None


def configured(config: AppConfig, argv: Sequence[str], section: str, name: str) -> Any:
    """Option `name` of `section` in the config file `argv` would use.

    The built-in default when the file is missing or invalid: the command run
    afterwards reports what is wrong with it.
    """
    default = config.section(section).option(name)
    assert default is not None, f"{section}.{name} is not an option"
    try:
        values = config.load(config.find(config_argument(argv)))
    except (ValueError, OSError):  # ConfigError is a ValueError
        return default.default
    return values[section].get(name, default.default)
