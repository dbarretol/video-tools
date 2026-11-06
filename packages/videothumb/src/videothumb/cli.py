"""Entry point: without arguments opens the GUI, with arguments runs the command line.

``videothumb config ...`` manages the defaults file of the command line.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from videoconsole import (
    Console,
    ConsoleOptions,
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
    configure_logging,
    execute,
    find_videos,
    hint_for,
    plan,
)
from videocore.config import Values, add_config_commands
from videocore.options import add_arguments, given_values, options_of
from videothumb import __version__
from videothumb.common import APP_NAME, Mode
from videothumb.config import CONFIG, CONSOLE_SECTION, RUN_SECTION
from videothumb.jobs import TASKS, Options, default_output
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

logger = logging.getLogger(__name__)

JSON_HELP = "write the plan and the results to stdout as one JSON document; messages go to stderr"

WORDING = Wording(
    verb="Processing",
    done="succeeded",
    skipped="skipped (output already exists)",
    redo_hint="use --overwrite to replace them",
    stages={"verify": "checking outputs"},
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Generate contact sheets and individual snapshots from videos. "
            "Run without arguments to open the desktop app."
        ),
        epilog=(
            "Defaults shown are the built-in ones; a config file can change them "
            f"(see `{APP_NAME} config --help`)."
        ),
        parents=[CONFIG.file_option()],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--video", type=Path, metavar="PATH", help="a single video")
    source.add_argument("--folder", type=Path, metavar="PATH", help="every video in a folder")
    parser.add_argument("--mode", choices=[m.value for m in Mode], required=True)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help=(
            "with --video: the sheet file or snapshot folder; with --folder: a folder that "
            "receives every output, recreating subfolders with -r (default: next to each video)"
        ),
    )

    add_arguments(parser, SheetOptions, SnapshotOptions)
    parser.add_argument(
        "--precise",
        action="store_true",
        help=(
            "frames at exact positions (slower); by default the nearest earlier keyframe is "
            "used, and exact positions only where keyframes would repeat"
        ),
    )
    add_arguments(parser, RunOptions, skip=("output",))
    existing = parser.add_mutually_exclusive_group()
    existing.add_argument(
        "--overwrite",
        dest="skip_existing",
        action="store_false",
        help="replace outputs that already exist (default)",
    )
    existing.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        help="leave videos whose sheet or snapshot folder already exists",
    )
    # Explicit: a store_false action (--overwrite) would otherwise make the default True.
    parser.set_defaults(skip_existing=False)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be done, without processing anything",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat incomplete outputs (DEGRADED, exit code 3) as failures (exit code 1)",
    )
    add_verbosity(parser)
    return parser


def add_verbosity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only show warnings, errors and what to do about them",
    )
    parser.add_argument("--json", action="store_true", help=JSON_HELP)
    parser.add_argument("--open", action="store_true", help="open the output folder when done")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from videothumb.gui.app import launch

        launch()
        return 0
    if argv[0] == "config":
        return config_main(argv[1:])

    try:
        args = build_parser().parse_args(argv)  # exits with 2 on invalid arguments
    except SystemExit:
        pause_if_alone()  # so that a window opened for us shows the error
        raise
    console = Console(quiet=args.quiet, json=args.json, tool=APP_NAME)
    console.record(mode=args.mode)
    args.desktop = Desktop(APP_NAME, console, WORDING, open_output=args.open)
    args.log_file = setup_logging(APP_NAME, console, verbose=args.verbose)
    exit_code = console.finish(run(args, console), log_file=args.log_file)
    return args.desktop.finish(exit_code)


def run(args: argparse.Namespace, console: Console) -> int:
    """Run the parsed command line; errors are reported, not raised."""
    try:
        config_path = CONFIG.find(args.config)
        if config_path is not None:
            logger.debug("Using defaults from %s", config_path)
        values = CONFIG.load(config_path)
        args.desktop.configure(ConsoleOptions(**values.get(CONSOLE_SECTION, {})))
        mode, options = Mode(args.mode), build_options(args, values, console)
        if args.video is not None:
            if not args.video.is_file():
                raise UsageError(f"Video not found: {args.video}")
            return run_single(args, console, mode, options)
        if not args.folder.is_dir():
            raise UsageError(f"Folder not found: {args.folder}")
        return run_batch(args, console, mode, options, values)
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


MODE_OPTIONS: dict[Mode, type[SheetOptions] | type[SnapshotOptions]] = {
    Mode.SHEET: SheetOptions,
    Mode.SNAPSHOTS: SnapshotOptions,
}


def config_main(argv: list[str]) -> int:
    """``videothumb config ACTION``: manage the defaults file."""
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME} config",
        description=(
            "Manage the defaults file of the command line. Keys are MODE.OPTION or "
            "run.OPTION, e.g. `config set sheet.columns 6`."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    add_config_commands(parser, CONFIG)
    args = parser.parse_args(argv)  # exits with 2 on invalid arguments
    configure_logging(APP_NAME, level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        return args.handler(args)
    except UsageError as exc:
        logger.debug("Usage error: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        logger.error("Failed: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED


def build_options(
    args: argparse.Namespace, values: Values | None = None, console: Console | None = None
) -> Options:
    """The options of the selected mode: flags, then the config file `values`, then defaults.

    Warns about flags that only apply to another mode.
    """
    mode = Mode(args.mode)
    cls = MODE_OPTIONS[mode]
    defaults = (values or {}).get(mode.value, {})
    given = given_values(cls, args)
    for other in MODE_OPTIONS.values():
        for opt in options_of(other):
            if opt.name not in given and getattr(args, opt.name, None) is not None:
                message = f"{opt.flags[-1]} does not apply to --mode {mode}; ignored"
                logger.warning("%s", message)
                if console is not None:
                    console.warning(message)
    settings = defaults | given
    if args.precise:
        settings["sampling"] = "precise"
    return cls(**settings)


def run_single(args: argparse.Namespace, console: Console, mode: Mode, options: Options) -> int:
    video = args.video
    target = args.output or default_output(video, mode)
    run = RunOptions(overwrite=not args.skip_existing)
    task, state = TASKS[mode], State(APP_NAME)
    planned = plan(task, [video], options, run, outputs=[target], speed=state.speed(task.name))
    if args.dry_run:
        print_plan(console, planned, WORDING, items=True)
        return EXIT_OK
    if not planned.pending:
        args.desktop.plan = planned
        console.record(plan=plan_document(planned))
        console.info(f"Skipped: {target} already exists (use --overwrite to replace it)")
        return EXIT_OK
    with args.desktop.running(planned), RunProgress(console, planned, WORDING) as progress:
        report = args.desktop.report = execute(planned, on_event=progress.on_event)
    state.learn_speed(task.name, report.measured_speed())
    record_report(console, report)
    (result,) = report.items
    if result.status is Status.CANCELLED:
        raise KeyboardInterrupt
    if result.status is Status.FAILED:
        console.error(str(result.error))
        console.hint(result.hint, args.log_file)
        return EXIT_FAILED
    noun = "Contact sheet" if mode is Mode.SHEET else "Snapshots"
    console.info(f"{noun} saved to {result.output}", "ok")
    for problem in result.problems:
        console.warning(f"incomplete output: {problem}")
    return report.exit_status(strict=args.strict)


def run_batch(
    args: argparse.Namespace, console: Console, mode: Mode, options: Options, values: Values
) -> int:
    settings = values.get(RUN_SECTION, {}) | given_values(RunOptions, args)
    settings.pop("output", None)  # -o is not a config option; it is read below
    run = RunOptions(output=args.output, overwrite=not args.skip_existing, **settings)
    videos = find_videos(args.folder, recursive=run.recursive)
    if not videos:
        console.info(f"No videos found in {args.folder}")
        return EXIT_OK
    task, state = TASKS[mode], State(APP_NAME)
    planned = plan(task, videos, options, run, source=args.folder, speed=state.speed(task.name))
    print_plan(console, planned, WORDING, items=args.dry_run)
    if args.dry_run:
        return EXIT_OK

    with args.desktop.running(planned), RunProgress(console, planned, WORDING) as progress:
        report = execute(planned, on_progress=progress.on_progress, on_event=progress.on_event)
    args.desktop.report = report
    state.learn_speed(task.name, report.measured_speed())
    print_report(console, report, WORDING, log_file=args.log_file)
    title = f"{APP_NAME} --folder {args.folder} --mode {mode}"
    resume = "run the same command with --skip-existing"
    report_run_lists(console, report, APP_NAME, title=title, resume=resume)
    return report.exit_status(strict=args.strict)
