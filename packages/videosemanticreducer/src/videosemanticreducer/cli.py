"""Command-line entry point for videosemanticreducer."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import re
import sys
from pathlib import Path
from typing import Any

from rich.text import Text

from videoconsole import (
    Console,
    Desktop,
    RunProgress,
    Wording,
    pause_if_alone,
    plan_document,
    print_plan,
    print_report,
    record_report,
    report_run_lists,
    setup_logging,
)
from videocore import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    RunOptions,
    State,
    Status,
    UsageError,
    collect_inputs,
    execute,
    expand_path,
    hint_for,
    plan,
    probe,
    read_input_list,
)
from videocore.commands import configured, with_default_task
from videocore.config import add_config_commands
from videocore.inputs import add_input_arguments, require_inputs
from videocore.options import add_arguments, given_values
from videosemanticreducer import __version__
from videosemanticreducer.compare import Comparison, compare_fingerprints
from videosemanticreducer.config import (
    CONFIG,
    CONSOLE_SECTION,
    ProcessDefaults,
    find_config,
    load_config,
    load_console_options,
    split_values,
)
from videosemanticreducer.fingerprint import compute_fingerprint, load_fingerprint, save_fingerprint
from videosemanticreducer.pipeline import (
    ARCHIVE,
    ProcessOptions,
    is_output_dir,
    resolve_output_dir,
)
from videosemanticreducer.status import ArchiveCheck, ArchiveStatus, find_archives

APP_NAME = "videosemanticreducer"

WORDING = Wording(
    verb="Archiving",
    done="archived",
    degraded="archived but incomplete",
    skipped="already archived",
    outdated="archived with other settings (not redone)",
    stages={
        "probe": "reading metadata",
        "snapshots": "summary video",
        "sheet": "contact sheet",
        "global_hashes": "fingerprint: global hashes",
        "keyframe_hashes": "fingerprint: keyframe hashes",
        "sha256": "file hash",
        "verify": "checking outputs",
        "manifest": "manifest",
    },
)
STATUS_STYLES = {
    ArchiveStatus.READY: "green",
    ArchiveStatus.INCOMPLETE: "yellow",
    ArchiveStatus.FAILED: "red",
    ArchiveStatus.CHANGED: "red",
    ArchiveStatus.MISSING: "yellow",
}

QUIET_HELP = "only show warnings, errors and what to do about them"
JSON_HELP = "write the plan and the results to stdout as one JSON document; messages go to stderr"

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Archive videos before deleting them: summary video, contact sheet, "
            "metadata report and perceptual fingerprint."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    parser.add_argument("-q", "--quiet", action="store_true", help=QUIET_HELP)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    # Shared by every subcommand, so `-v` works before or after it. SUPPRESS keeps a
    # subcommand from resetting a `-v` given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show debug messages",
    )
    common.add_argument(
        "-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help=QUIET_HELP
    )

    config_option = argparse.ArgumentParser(add_help=False, parents=[common, CONFIG.file_option()])
    json_option = argparse.ArgumentParser(add_help=False)
    json_option.add_argument("--json", action="store_true", help=JSON_HELP)
    open_option = argparse.ArgumentParser(add_help=False)
    open_option.add_argument("--open", action="store_true", help="open the output folder when done")
    open_option.add_argument(
        "--no-progress",
        dest="progress",
        action="store_false",
        help="no live progress bar; one line per video instead",
    )

    # Option defaults are None so that values from the config file apply unless a flag is given.
    process = commands.add_parser(
        "archive",
        parents=[config_option, json_option, open_option],
        help="archive videos: summary, contact sheet, report, fingerprint and manifest",
        epilog="Defaults shown are the built-in ones; a config file can change them.",
    )
    add_input_arguments(process, "videos or folders (one video: -o is its archive folder)")
    add_arguments(process, RunOptions, ProcessOptions)
    add_sheet_grid(process)
    add_legacy_options(process)
    process.add_argument(
        "--dry-run", action="store_true", help="show what would be done, without archiving"
    )
    process.add_argument(
        "--strict",
        action="store_true",
        help="treat incomplete outputs (DEGRADED, exit code 3) as failures (exit code 1)",
    )
    existing = process.add_mutually_exclusive_group()
    existing.add_argument(
        "--skip-existing",
        dest="overwrite",
        action="store_false",
        help="skip videos whose archive is already complete (default)",
    )
    existing.add_argument(
        "--overwrite",
        dest="overwrite",
        action="store_true",
        help="archive every video again, even when its archive is complete",
    )
    process.set_defaults(handler=cmd_archive, overwrite=False)

    config = commands.add_parser(
        "config",
        help="manage the defaults file of `archive`",
        description=(
            "Manage the defaults file of `archive`. Keys are option names, "
            "e.g. `config set num_snapshots 100` or `config set console.pause false`."
        ),
    )
    add_config_commands(config, CONFIG, parents=[common])

    fingerprint = commands.add_parser(
        "fingerprint", parents=[common], help="only compute a video's fingerprint"
    )
    add_input_arguments(fingerprint)
    fingerprint.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    fingerprint.add_argument(
        "-o",
        "--output",
        type=Path,
        help="JSON path, for a single video (default: <video>.fingerprint.json next to each)",
    )
    fingerprint.set_defaults(handler=cmd_fingerprint)

    compare = commands.add_parser(
        "compare",
        parents=[common],
        help="compare two fingerprint files",
        epilog="Exit code: 0 same video, 1 different or uncertain, 2 error.",
    )
    compare.add_argument("fingerprint1", type=Path)
    compare.add_argument("fingerprint2", type=Path)
    compare.set_defaults(handler=cmd_compare)

    verify = commands.add_parser(
        "verify",
        parents=[common],
        help="check a video against a saved fingerprint",
        epilog="Exit code: 0 same video, 1 different or uncertain, 2 error.",
    )
    verify.add_argument("video", type=Path)
    verify.add_argument("fingerprint", type=Path)
    verify.set_defaults(handler=cmd_verify)

    status = commands.add_parser(
        "status",
        parents=[config_option, json_option],
        help="check that archives are complete and unchanged, before deleting originals",
        description=(
            "Check archives made by `archive`: the manifest says they are complete, every "
            "artifact is still there and unchanged, and the original, if still present, is "
            "the video that was archived."
        ),
        epilog="Exit code: 0 every archive is ready (originals can be deleted), 1 otherwise.",
    )
    add_input_arguments(status, "videos, archive folders or folders")
    status.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="the output folder given to `archive`, if any (default: from the config file)",
    )
    status.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    status.add_argument(
        "--full",
        action="store_true",
        help="also compare the SHA-256 of every file instead of only sizes (reads everything)",
    )
    status.set_defaults(handler=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit code.

    0 success, 1 a video failed (or `verify`/`compare` found no match, or `status`
    found an archive that is not ready), 3 some outputs are incomplete (DEGRADED),
    2 invalid arguments, inputs or config, 130 interrupted with Ctrl+C.
    """
    argv, deprecations = translate_legacy(sys.argv[1:] if argv is None else argv)
    argv = with_default_task(
        argv, COMMAND_NAMES, lambda: default_task(argv), global_flags=GLOBAL_FLAGS
    )
    parser = build_parser()
    try:
        args = parser.parse_args(argv)  # exits with 2 on invalid arguments
    except SystemExit:
        pause_if_alone()  # so that a window opened for us shows the error
        raise
    if args.command is None:
        parser.print_help()
        pause_if_alone()
        return EXIT_OK

    json = getattr(args, "json", False)
    progress = getattr(args, "progress", True)
    console = Console(quiet=args.quiet, json=json, progress=progress, tool=APP_NAME)
    args.console = console
    console.record(command=args.command)
    for warning in [*deprecations, *getattr(args, "deprecations", [])]:
        console.warning(warning)
    desktop = Desktop(APP_NAME, console, WORDING, open_output=getattr(args, "open", False))
    args.desktop = desktop
    log_file = args.log_file = setup_logging(APP_NAME, console, verbose=args.verbose)
    logger.debug("Logging to %s", log_file)
    return desktop.finish(console.finish(run(args, console), log_file=log_file))


def run(args: argparse.Namespace, console: Console) -> int:
    """Run the parsed subcommand; errors are reported, not raised."""
    try:
        return args.handler(args)
    except KeyboardInterrupt as exc:
        logger.info("Interrupted", exc_info=True)
        console.record_error(exc)
        console.detail("Interrupted; unfinished files were removed.", "warning")
        return EXIT_INTERRUPTED
    except UsageError as exc:
        logger.error("Usage error: %s", exc, exc_info=True)
        console.record_error(exc)
        console.error(str(exc))
        return EXIT_USAGE
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("Failed: %s", exc, exc_info=True)
        hint = hint_for(exc)
        console.record_error(exc, hint)
        console.error(str(exc))
        console.hint(hint, args.log_file)
        return EXIT_FAILED


def process_settings(args: argparse.Namespace) -> ProcessDefaults:
    """Config file values, overridden by the flags given on the command line."""
    config_path = find_config(args.config)
    if config_path is not None:
        logger.debug("Using defaults from %s", config_path)
    args.desktop.configure(load_console_options(config_path))
    given = given_values(ProcessOptions, args) | given_values(RunOptions, args)
    try:
        return split_values(given, load_config(config_path))
    except ValueError as exc:  # flags are checked by argparse, so this is unexpected
        raise UsageError(f"Invalid settings: {exc}") from exc


def cmd_archive(args: argparse.Namespace) -> int:
    console: Console = args.console
    settings = process_settings(args)
    options = settings.options
    run = dataclasses.replace(settings.run, overwrite=args.overwrite)
    # How fast archiving ran here before, to estimate how long this run takes.
    state = State(APP_NAME)
    speed = state.speed(ARCHIVE.name)

    require_inputs(args)
    inputs = collect_inputs(
        args.paths, args.input_list, recursive=run.recursive, skip_dir=is_output_dir
    )
    for warning in inputs.warnings:
        console.warning(warning)

    if inputs.single_file:
        (video,) = inputs.videos
        output = resolve_output_dir(video, run.output, batch=False)
        planned = plan(ARCHIVE, [video], options, run, outputs=[output], speed=speed)
        if args.dry_run:
            print_plan(console, planned, WORDING, items=True)
            return EXIT_OK
        if not planned.pending:
            args.desktop.plan = planned
            console.record(plan=plan_document(planned))
            how = "already" if planned.items[0].state is Status.SKIPPED else "with other settings"
            console.info(f"{video} is {how} archived in {output}; use --overwrite to redo it")
            return EXIT_OK
        with args.desktop.running(planned), RunProgress(console, planned, WORDING) as progress:
            report = args.desktop.report = execute(planned, on_event=progress.on_event)
        state.learn_speed(ARCHIVE.name, report.measured_speed())
        record_report(console, report)
        (result,) = report.items
        if result.status is Status.CANCELLED:
            raise KeyboardInterrupt
        if result.status is Status.FAILED:
            console.error(f"Could not archive {video}: {result.error}")
            console.hint(result.hint, args.log_file)
        elif result.status is Status.DEGRADED:
            console.warning(f"archived {video}, but incomplete: {'; '.join(result.problems)}")
            console.detail("Incomplete archives are not marked complete; the next run redoes them")
        else:
            console.info(f"Archived {video} in {result.output}", "ok")
        return report.exit_status(strict=args.strict)
    if not len(inputs):
        console.info("No videos found")
        return EXIT_OK
    planned = plan(
        ARCHIVE,
        inputs.videos,
        options,
        run,
        source=inputs.roots,
        invalid=inputs.invalid,
        speed=speed,
    )
    print_plan(console, planned, WORDING, items=args.dry_run)
    if args.dry_run:
        return EXIT_OK
    if planned.pending:
        console.info(f"Up to {planned.workers} videos processed in parallel")

    with args.desktop.running(planned), RunProgress(console, planned, WORDING) as progress:
        report = execute(planned, on_progress=progress.on_progress, on_event=progress.on_event)
    args.desktop.report = report
    state.learn_speed(ARCHIVE.name, report.measured_speed())
    print_report(console, report, WORDING, log_file=args.log_file)
    if report.count(Status.SKIPPED) or report.count(Status.OUTDATED):
        console.info("Use --overwrite to archive the skipped videos again")
    if report.degraded:
        console.detail("Incomplete archives are not marked complete; the next run redoes them")
    resume = "run the same command again (archived videos are skipped)"
    title = f"{APP_NAME} archive {describe_inputs(args)}"
    report_run_lists(console, report, APP_NAME, title=title, resume=resume)
    return report.exit_status(strict=args.strict)


def cmd_fingerprint(args: argparse.Namespace) -> int:
    console: Console = args.console
    require_inputs(args)
    inputs = collect_inputs(args.paths, args.input_list, recursive=args.recursive)
    for warning in inputs.warnings:
        console.warning(warning)
    if args.output is not None and len(inputs) != 1:
        raise UsageError("-o names one fingerprint file; give a single video")
    failed = 0
    for video in inputs.videos:
        output = args.output or video.with_name(f"{video.name}.fingerprint.json")
        try:
            save_fingerprint(compute_fingerprint(probe(video)), output)
        except (OSError, ValueError, RuntimeError) as exc:
            if inputs.single_file:
                raise
            logger.error("Fingerprint of %s failed: %s", video, exc, exc_info=True)
            console.error(f"{video}: {exc}")
            console.hint(hint_for(exc), None)
            failed += 1
            continue
        console.info(f"Fingerprint saved to {output}", "ok")
    for entry in inputs.invalid:
        console.error(f"{entry.path}: {entry.reason}")
        failed += 1
    if failed and not inputs.single_file:
        console.detail(f"Details: {args.log_file}", "dim")
    return EXIT_FAILED if failed else EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        fp1, fp2 = load_fingerprint(args.fingerprint1), load_fingerprint(args.fingerprint2)
    except (OSError, ValueError) as exc:
        raise UsageError(str(exc)) from exc
    report = compare_fingerprints(fp1, fp2)
    print_comparison(
        "Fingerprint comparison",
        [("File 1", fp1.get("filename")), ("File 2", fp2.get("filename"))],
        report,
    )
    return EXIT_OK if report.is_match else EXIT_FAILED


def cmd_verify(args: argparse.Namespace) -> int:
    # Exit code 1 means "does not match", so every error is reported as 2 instead.
    try:
        saved = load_fingerprint(args.fingerprint)
        args.console.info(f"Computing fingerprint of {args.video}")
        current = compute_fingerprint(probe(args.video))
    except (OSError, ValueError, RuntimeError) as exc:
        raise UsageError(f"Could not verify {args.video}: {exc}") from exc
    report = compare_fingerprints(saved, current)
    print_comparison(
        "Verification",
        [("Original", saved.get("filename")), ("Checked", args.video.name)],
        report,
    )
    if report.is_match:
        verdict = "The video matches the original with high confidence."
    elif report.match_level in ("HIGH", "MEDIUM"):
        verdict = "The video looks like the original but has significant differences."
    else:
        verdict = "The video probably does not match the original."
    print(f"  >>> {verdict}")
    print("=" * 60)
    return EXIT_OK if report.is_match else EXIT_FAILED


def cmd_status(args: argparse.Namespace) -> int:
    output = args.output
    if output is None:
        output = load_config(find_config(args.config)).run.output
    require_inputs(args)
    paths = [path for text in args.paths for path in expand_path(text)]
    for source in args.input_list:
        listed, warnings = read_input_list(source)
        paths += listed
        for warning in warnings:
            args.console.warning(warning)
    checks = []
    for path in dict.fromkeys(paths):
        if not path.exists():
            missing = ArchiveCheck(path.parent, path.stem, path, ArchiveStatus.MISSING)
            missing.details.append("not found")
            checks.append(missing)
            continue
        checks += find_archives(path, output=output, recursive=args.recursive, full=args.full)
    console: Console = args.console
    console.record(archives=[archive_document(check) for check in checks])
    if not checks:
        console.notice("No videos or archives found")
        return EXIT_FAILED

    width = max(len(status.value) for status in ArchiveStatus)
    for check in checks:
        if console.quiet and check.status is ArchiveStatus.READY:
            continue
        line = Text(f"{check.status.upper():<{width}}", style=STATUS_STYLES[check.status])
        console.out.print(line + Text(f"  {check.label}"))
        for detail in check.details:
            console.out.print(Text(f"{'':<{width}}    {detail}"))
    counts = [(status, sum(c.status is status for c in checks)) for status in ArchiveStatus]
    parts = [
        f"{count} {'ready to delete' if status is ArchiveStatus.READY else status.value}"
        for status, count in counts
        if count
    ]
    console.notice(f"{len(checks)} checked: " + ", ".join(parts))
    if not args.full and counts[0][1]:
        console.info("Only sizes were compared; use --full to also compare SHA-256 hashes.")
    ready = all(check.status is ArchiveStatus.READY for check in checks)
    return EXIT_OK if ready else EXIT_FAILED


COMMAND_NAMES = ("archive", "fingerprint", "compare", "verify", "status", "config")
GLOBAL_FLAGS = ("-v", "--verbose", "-q", "--quiet")
"""The flags that can come before the command."""


def default_task(argv: list[str]) -> str:
    """The command run when none is given: ``console.default_task`` of the config file."""
    return configured(CONFIG, argv, CONSOLE_SECTION, "default_task")


def translate_legacy(argv: list[str]) -> tuple[list[str], list[str]]:
    """The command line with the ``process`` command renamed ``archive``, and the warning."""
    for index, arg in enumerate(argv):
        if not arg.startswith("-"):  # the command: only flags without values come before it
            if arg == "process":
                warning = f"`{APP_NAME} process` is now `{APP_NAME} archive`"
                return [*argv[:index], "archive", *argv[index + 1 :]], [warning]
            break
    return argv, []


GRID = re.compile(r"\s*(\d+)\s*[xX]\s*(\d+)\s*")


def parse_grid(text: str) -> tuple[int, int]:
    """``"12x8"`` -> (12, 8): columns and rows."""
    match = GRID.fullmatch(text)
    if match is None or int(match[1]) == 0 or int(match[2]) == 0:
        raise argparse.ArgumentTypeError(f"expected COLUMNSxROWS such as 12x12, got {text!r}")
    return int(match[1]), int(match[2])


class _SetGrid(argparse.Action):
    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None):
        namespace.sheet_columns, namespace.sheet_rows = values


def add_sheet_grid(parser: argparse.ArgumentParser) -> None:
    defaults = ProcessOptions()
    group = parser.add_argument_group("contact sheet")
    group.add_argument(
        "--grid",
        type=parse_grid,
        action=_SetGrid,
        metavar="CxR",
        help=(
            "columns and rows of the contact sheet "
            f"(default: {defaults.sheet_columns}x{defaults.sheet_rows})"
        ),
    )
    parser.set_defaults(sheet_columns=None, sheet_rows=None)


class _Deprecated(argparse.Action):
    """A flag of earlier versions: sets what its replacement sets, and records a warning."""

    def __init__(self, option_strings: list[str], dest: str, *, new: str, **kwargs: Any) -> None:
        self.new = new
        super().__init__(option_strings, dest, help=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: Any = None):
        setattr(namespace, self.dest, self.const if self.nargs == 0 else values)
        warnings = getattr(namespace, "deprecations", None) or []
        warning = f"{option_string} is deprecated and will be removed; use {self.new}"
        namespace.deprecations = [*warnings, warning]


def add_legacy_options(parser: argparse.ArgumentParser) -> None:
    """The flags of `archive` before 0.2, hidden from the help."""
    renamed = {
        "--video": ("summary", True, "--summary"),
        "--no-video": ("summary", False, "--no-summary"),
        "--thumbnail": ("sheet", True, "--sheet"),
        "--no-thumbnail": ("sheet", False, "--no-sheet"),
    }
    for flag, (dest, value, new) in renamed.items():
        parser.add_argument(flag, dest=dest, action=_Deprecated, nargs=0, const=value, new=new)
    for flag, dest in (("--thumb-cols", "sheet_columns"), ("--thumb-rows", "sheet_rows")):
        parser.add_argument(flag, dest=dest, action=_Deprecated, type=_positive, new="--grid CxR")


def _positive(text: str) -> int:
    if not text.isdigit() or int(text) == 0:
        raise argparse.ArgumentTypeError(f"expected a whole number greater than 0, got {text!r}")
    return int(text)


def archive_document(check: ArchiveCheck) -> dict[str, object]:
    return {
        "video": check.video,
        "archive": check.folder,
        "name": check.name,
        "status": check.status.value.replace(" ", "_"),
        "details": check.details,
    }


def describe_inputs(args: argparse.Namespace) -> str:
    """The inputs of the command line, for the title of the lists of videos to retry."""
    parts = [*args.paths, *(f"--input-list {name}" for name in args.input_list)]
    return " ".join(parts)


def print_comparison(title: str, files: list[tuple[str, object]], report: Comparison) -> None:
    global_sim = (
        "n/a (different fingerprint versions or missing)"
        if report.global_similarity is None
        else f"{report.global_similarity}%"
    )
    rows = [
        *files,
        ("Match level", report.match_level),
        ("Confidence", f"{report.confidence}%"),
        ("SHA-256 match", "yes" if report.sha256_match else "no"),
        ("Global similarity", global_sim),
        ("Keyframes (close)", f"{report.keyframes_close_pct}%"),
        ("Duration diff", f"{report.duration_diff_pct}%"),
    ]
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)
    for label, value in rows:
        print(f"  {label + ':':<19}{value}")
    print("=" * 60)
