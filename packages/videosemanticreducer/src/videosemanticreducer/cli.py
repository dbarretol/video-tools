"""Command-line entry point for videosemanticreducer."""

from __future__ import annotations

import argparse
import dataclasses
import logging
from pathlib import Path

from videocore import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    ItemResult,
    Plan,
    ProgressEvent,
    RunOptions,
    State,
    Status,
    UsageError,
    configure_logging,
    default_runs_dir,
    execute,
    find_videos,
    hint_for,
    plan,
    probe,
    write_run_lists,
)
from videocore.config import add_config_commands
from videocore.options import add_arguments, given_values
from videosemanticreducer import __version__
from videosemanticreducer.compare import Comparison, compare_fingerprints
from videosemanticreducer.config import (
    CONFIG,
    ProcessDefaults,
    find_config,
    load_config,
    split_values,
)
from videosemanticreducer.fingerprint import compute_fingerprint, load_fingerprint, save_fingerprint
from videosemanticreducer.pipeline import (
    ARCHIVE,
    ProcessOptions,
    is_output_dir,
    resolve_output_dir,
)
from videosemanticreducer.status import ArchiveStatus, find_archives

APP_NAME = "videosemanticreducer"

STAGE_LABELS = {
    "probe": "reading metadata",
    "snapshots": "summary video",
    "thumbnail": "thumbnail grid",
    "global_hashes": "fingerprint: global hashes",
    "keyframe_hashes": "fingerprint: keyframe hashes",
    "sha256": "file hash",
    "verify": "checking outputs",
    "manifest": "manifest",
}

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Archive videos before deleting them: summary video, thumbnail grid, "
            "metadata report and perceptual fingerprint."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
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

    config_option = argparse.ArgumentParser(add_help=False, parents=[common, CONFIG.file_option()])

    # Option defaults are None so that values from the config file apply unless a flag is given.
    process = commands.add_parser(
        "process",
        parents=[config_option],
        help="archive a video or every video in a folder",
        epilog="Defaults shown are the built-in ones; a config file can change them.",
    )
    process.add_argument("input", type=Path, help="video file or folder")
    add_arguments(process, RunOptions, ProcessOptions)
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
    process.set_defaults(handler=cmd_process, overwrite=False)

    config = commands.add_parser(
        "config",
        help="manage the defaults file of `process`",
        description=(
            "Manage the defaults file of `process`. Keys are option names, "
            "e.g. `config set snapshot_fps 2`."
        ),
    )
    add_config_commands(config, CONFIG, parents=[common])

    fingerprint = commands.add_parser(
        "fingerprint", parents=[common], help="only compute a video's fingerprint"
    )
    fingerprint.add_argument("video", type=Path)
    fingerprint.add_argument(
        "-o", "--output", type=Path, help="JSON path (default: <video>.fingerprint.json)"
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
        parents=[config_option],
        help="check that archives are complete and unchanged, before deleting originals",
        description=(
            "Check archives made by `process`: the manifest says they are complete, every "
            "artifact is still there and unchanged, and the original, if still present, is "
            "the video that was archived."
        ),
        epilog="Exit code: 0 every archive is ready (originals can be deleted), 1 otherwise.",
    )
    status.add_argument(
        "paths", nargs="+", type=Path, metavar="PATH", help="video, archive folder or folder"
    )
    status.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="the output folder given to `process`, if any (default: from the config file)",
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
    parser = build_parser()
    args = parser.parse_args(argv)  # exits with 2 on invalid arguments
    if args.command is None:
        parser.print_help()
        return EXIT_OK

    level = logging.DEBUG if args.verbose else logging.INFO
    log_file = configure_logging(APP_NAME, level=level, console=True)
    logger.debug("Logging to %s", log_file)
    args.log_file = log_file
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        logger.error("Interrupted; unfinished files were removed")
        return EXIT_INTERRUPTED
    except UsageError as exc:
        logger.error("%s", exc)
        logger.debug("Details", exc_info=True)
        return EXIT_USAGE
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        if hint := hint_for(exc):
            logger.error("  Hint: %s", hint)
        logger.debug("Details", exc_info=True)
        logger.info("Details: %s", log_file)
        return EXIT_FAILED


def process_settings(args: argparse.Namespace) -> ProcessDefaults:
    """Config file values, overridden by the flags given on the command line."""
    config_path = find_config(args.config)
    if config_path is not None:
        logger.info("Using defaults from %s", config_path)
    given = given_values(ProcessOptions, args) | given_values(RunOptions, args)
    try:
        return split_values(given, load_config(config_path))
    except ValueError as exc:  # flags are checked by argparse, so this is unexpected
        raise UsageError(f"Invalid settings: {exc}") from exc


def cmd_process(args: argparse.Namespace) -> int:
    settings = process_settings(args)
    options = settings.options
    run = dataclasses.replace(settings.run, overwrite=args.overwrite)
    # How fast archiving ran here before, to estimate how long this run takes.
    state = State(APP_NAME)
    speed = state.speed(ARCHIVE.name)

    if args.input.is_file():
        output = resolve_output_dir(args.input, run.output, batch=False)
        planned = plan(ARCHIVE, [args.input], options, run, outputs=[output], speed=speed)
        if args.dry_run:
            print_plan(planned)
            return EXIT_OK
        if not planned.pending:
            how = "already" if planned.items[0].state is Status.SKIPPED else "with other settings"
            logger.info(
                "%s is %s archived in %s; use --overwrite to redo it", args.input, how, output
            )
            return EXIT_OK
        report = execute(planned, on_event=stage_logger(logging.INFO))
        state.learn_speed(ARCHIVE.name, report.measured_speed())
        (result,) = report.items
        if result.status is Status.CANCELLED:
            raise KeyboardInterrupt
        if result.status is Status.FAILED:
            logger.error("Could not archive %s: %s", args.input, result.error)
            log_failure_details(result, args.log_file)
        elif result.status is Status.DEGRADED:
            logger.warning(
                "Archived %s, but incomplete: %s", args.input, "; ".join(result.problems)
            )
        return report.exit_status(strict=args.strict)
    if not args.input.is_dir():
        raise UsageError(f"Input not found: {args.input}")

    videos = find_videos(args.input, recursive=run.recursive, skip_dir=is_output_dir)
    if not videos:
        logger.warning("No videos found in %s", args.input)
        return EXIT_OK
    planned = plan(ARCHIVE, videos, options, run, source=args.input, speed=speed)
    if args.dry_run:
        print_plan(planned)
        return EXIT_OK
    for line in planned.summary():
        logger.info(line)
    if planned.pending:
        logger.info("Up to %d videos processed in parallel", planned.workers)

    def progress(done: int, total: int, result: ItemResult) -> None:
        status = result.status.value if result.status is Status.OK else result.status.upper()
        logger.info("[%d/%d] %s: %s", done, total, result.video.name, status)

    report = execute(planned, on_progress=progress, on_event=stage_logger(logging.DEBUG))
    state.learn_speed(ARCHIVE.name, report.measured_speed())
    failed = report.failed
    skipped, outdated = report.count(Status.SKIPPED), report.count(Status.OUTDATED)
    summary = f"Done: {report.count(Status.OK)} archived"
    if degraded := report.degraded:
        summary += f", {len(degraded)} archived but incomplete"
    summary += f", {skipped} already archived"
    if outdated:
        summary += f", {outdated} archived with other settings (not redone)"
    summary += f", {len(failed)} failed"
    if cancelled := report.count(Status.CANCELLED):
        summary += f", {cancelled} not finished"
    logger.info(summary)
    if skipped or outdated:
        logger.info("Use --overwrite to archive the skipped videos again")
    for result in degraded:
        logger.warning("Incomplete: %s (%s)", result.video, "; ".join(result.problems))
    for result in failed:
        logger.error("Failed: %s (%s)", result.video, result.error)
        if result.hint:
            logger.error("  Hint: %s", result.hint)
    if failed:
        logger.info("Details: %s", args.log_file)
    if degraded:
        logger.info("Incomplete archives are not marked complete; the next run redoes them")
    if report.interrupted:
        logger.error("Interrupted; unfinished files were removed")
    lists = write_run_lists(
        report, default_runs_dir(APP_NAME), title=f"{APP_NAME} process {args.input}"
    )
    if lists.failed:
        logger.info("Videos to retry are listed in %s", lists.failed)
    if lists.pending:
        logger.info(
            "Unfinished videos are listed in %s; run the same command again to resume "
            "(archived videos are skipped)",
            lists.pending,
        )
    return report.exit_status(strict=args.strict)


def log_failure_details(result: ItemResult, log_file: Path) -> None:
    if result.hint:
        logger.error("  Hint: %s", result.hint)
    logger.info("Details: %s", log_file)


def stage_logger(level: int):
    """An ``on_event`` callback that logs when each video enters each stage."""
    seen: set[tuple[Path, str]] = set()

    def on_event(event: ProgressEvent) -> None:
        if event.kind != "stage" or event.stage is None or (event.item, event.stage) in seen:
            return
        seen.add((event.item, event.stage))
        label = STAGE_LABELS.get(event.stage, event.stage)
        logger.log(level, "%s: %s", Path(event.item).name, label)

    return on_event


def print_plan(planned: Plan) -> None:
    for line in [*planned.summary(), *planned.item_lines()]:
        print(line)


def cmd_fingerprint(args: argparse.Namespace) -> int:
    info = probe(args.video)
    output = args.output or args.video.with_name(f"{args.video.name}.fingerprint.json")
    save_fingerprint(compute_fingerprint(info), output)
    logger.info("Fingerprint saved to %s", output)
    return 0


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
        logger.info("Computing fingerprint of %s", args.video)
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
    checks = []
    for path in args.paths:
        if not path.exists():
            raise UsageError(f"Not found: {path}")
        checks += find_archives(path, output=output, recursive=args.recursive, full=args.full)
    if not checks:
        print("No videos or archives found")
        return EXIT_FAILED

    width = max(len(status.value) for status in ArchiveStatus)
    for check in checks:
        print(f"{check.status.upper():<{width}}  {check.label}")
        for detail in check.details:
            print(f"{'':<{width}}    {detail}")
    counts = [(status, sum(c.status is status for c in checks)) for status in ArchiveStatus]
    parts = [
        f"{count} {'ready to delete' if status is ArchiveStatus.READY else status.value}"
        for status, count in counts
        if count
    ]
    print(f"{len(checks)} checked: " + ", ".join(parts))
    if not args.full and counts[0][1]:
        print("Only sizes were compared; use --full to also compare SHA-256 hashes.")
    ready = all(check.status is ArchiveStatus.READY for check in checks)
    return EXIT_OK if ready else EXIT_FAILED


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
