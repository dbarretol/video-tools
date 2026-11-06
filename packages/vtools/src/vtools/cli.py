"""``vtools COMMAND ...``: run the command with the tool that has it.

The tools stay independent programs; this only routes. Flags that may come
before the command, such as ``-v``, are moved after it, since every command
accepts them there. Arguments that start with something other than a command
go to videothumb, which runs its default task (``vtools clip.mp4`` makes a
contact sheet).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from videoconsole import pause_if_alone
from videocore import EXIT_OK, EXIT_USAGE
from videocore.commands import help_epilog, with_help
from videosemanticreducer import __version__ as vsr_version
from videosemanticreducer import cli as vsr
from videothumb import __version__ as thumb_version
from videothumb import cli as thumb
from vtools import __version__

THUMB_COMMANDS = {
    "sheet": "a contact sheet per video",
    "frames": "a folder of full-size frames per video",
    "pack": "the contact sheet and the frames of each video in one ZIP",
    "gui": "open the desktop app",
}
VSR_COMMANDS = {
    "archive": "archive videos before deleting them: summary, contact sheet, report, fingerprint",
    "fingerprint": "only compute the fingerprint of videos",
    "compare": "compare two fingerprint files",
    "verify": "check a video against a saved fingerprint",
    "status": "check that archives are complete and unchanged, before deleting originals",
}
OLD_COMMANDS = {"snapshots": thumb, "process": vsr}
"""Commands of earlier versions; each tool translates them with a warning."""
CONFIGS = {"thumb": thumb, "vsr": vsr}
GLOBAL_FLAGS = ("-v", "--verbose", "-q", "--quiet")
COMMANDS = (*THUMB_COMMANDS, *VSR_COMMANDS, *OLD_COMMANDS, "config")
EXAMPLES = (
    ("vtools clip.mp4", "clip_sheet.jpg next to the video"),
    ("vtools frames D:\\Videos --count 20", "20 full-size frames of every video in the folder"),
    (
        "vtools pack D:\\Videos -r",
        "one ZIP per video, subfolders included, with its sheet and frames",
    ),
    (
        "vtools archive movie.mp4",
        "movie_summary\\: summary video, contact sheet, report and "
        "fingerprint, before deleting the video",
    ),
    ("vtools status D:\\Videos", "whether every archive is complete"),
    ("vtools gui", "the desktop app"),
)


def main(argv: Sequence[str] | None = None) -> int:
    argv = with_help(sys.argv[1:] if argv is None else argv, COMMANDS, global_flags=GLOBAL_FLAGS)
    flags = []
    while argv and argv[0] in GLOBAL_FLAGS:
        flags.append(argv.pop(0))
    if not argv or argv[0] in ("-h", "--help"):
        print(help_text())
        if not argv:
            pause_if_alone()
        return EXIT_OK
    if argv[0] == "--version":
        print(
            f"vtools {__version__} (videothumb {thumb_version}, videosemanticreducer {vsr_version})"
        )
        return EXIT_OK
    command, rest = argv[0], argv[1:]
    if command == "config":
        return config(rest)
    if command in VSR_COMMANDS or OLD_COMMANDS.get(command) is vsr:
        return vsr.main([command, *flags, *rest])
    if command in THUMB_COMMANDS or command in OLD_COMMANDS:
        return thumb.main([command, *flags, *rest])
    return thumb.main([*argv, *flags])  # its default task


def config(argv: list[str]) -> int:
    if argv and argv[0] in CONFIGS:
        return CONFIGS[argv[0]].main(["config", *argv[1:]])
    usage = (
        "usage: vtools config {thumb,vsr} ACTION ...\n\n"
        "Each tool has its own defaults file: `vtools config thumb show`, "
        "`vtools config vsr set num_snapshots 100`; `vtools config vsr --help` lists the actions."
    )
    if argv and argv[0] in ("-h", "--help"):
        print(usage)
        return EXIT_OK
    print(usage, file=sys.stderr)
    return EXIT_USAGE


def help_text() -> str:
    width = max(map(len, [*THUMB_COMMANDS, *VSR_COMMANDS, "config"]))

    def rows(commands: dict[str, str]) -> list[str]:
        return [f"  {name:<{width}}  {text}" for name, text in commands.items()]

    return "\n".join(
        [
            "usage: vtools COMMAND [options] | vtools PATH... | vtools --version",
            "",
            "Contact sheets, frames and video archives in one command.",
            "",
            "videothumb:",
            *rows(THUMB_COMMANDS),
            "",
            "videosemanticreducer:",
            *rows(VSR_COMMANDS),
            "",
            *rows({"config": "the defaults file of a tool: `vtools config {thumb,vsr} ...`"}),
            "",
            help_epilog(
                EXAMPLES,
                "`vtools PATH...` runs the default task of videothumb (`sheet` unless its config "
                "file sets console.default_task). `vtools help COMMAND` or `vtools COMMAND "
                "--help` shows the options of a command.",
            ),
        ]
    )
