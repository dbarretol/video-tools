"""Command-line entry point for videosemanticreducer."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from videocore import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    BatchResult,
    UsageError,
    configure_logging,
    default_workers,
    find_videos,
    probe,
)
from videocore.options import add_arguments, given_values
from videosemanticreducer import __version__
from videosemanticreducer.compare import Comparison, compare_fingerprints
from videosemanticreducer.config import (
    CONFIG_ENV,
    ConfigError,
    ProcessDefaults,
    RunDefaults,
    default_config_path,
    find_config,
    load_config,
    render_template,
    split_values,
)
from videosemanticreducer.fingerprint import compute_fingerprint, load_fingerprint, save_fingerprint
from videosemanticreducer.pipeline import (
    ARCHIVE_OUTDATED,
    ProcessOptions,
    archive_state,
    is_output_dir,
    process_batch,
    process_video,
    resolve_output_dir,
)

APP_NAME = "videosemanticreducer"

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

    config_option = argparse.ArgumentParser(add_help=False, parents=[common])
    config_option.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help=f"YAML file with defaults (default: ${CONFIG_ENV} or {default_config_path()})",
    )

    # Option defaults are None so that values from the config file apply unless a flag is given.
    process = commands.add_parser(
        "process",
        parents=[config_option],
        help="archive a video or every video in a folder",
        epilog="Defaults shown are the built-in ones; a config file can change them.",
    )
    process.add_argument("input", type=Path, help="video file or folder")
    add_arguments(process, RunDefaults, ProcessOptions)
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

    config = commands.add_parser("config", help="manage the defaults file of `process`")
    actions = config.add_subparsers(dest="action", metavar="ACTION", required=True)
    init = actions.add_parser(
        "init", parents=[config_option], help="write a commented config file to edit"
    )
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(handler=cmd_config_init)
    show = actions.add_parser(
        "show", parents=[config_option], help="print the defaults in effect and their source"
    )
    show.set_defaults(handler=cmd_config_show)
    path = actions.add_parser(
        "path", parents=[common], help="print where the config file is looked up"
    )
    path.set_defaults(handler=cmd_config_path)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit code.

    0 success, 1 a video failed (or `verify`/`compare` found no match),
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
        logger.debug("Details", exc_info=True)
        return EXIT_FAILED


def process_settings(args: argparse.Namespace) -> ProcessDefaults:
    """Config file values, overridden by the flags given on the command line."""
    config_path = find_config(args.config)
    if config_path is not None:
        logger.info("Using defaults from %s", config_path)
    given = given_values(ProcessOptions, args) | given_values(RunDefaults, args)
    try:
        return split_values(given, load_config(config_path))
    except ValueError as exc:  # flags are checked by argparse, so this is unexpected
        raise UsageError(f"Invalid settings: {exc}") from exc


def cmd_process(args: argparse.Namespace) -> int:
    settings = process_settings(args)
    options, run = settings.options, settings.run

    if args.input.is_file():
        output = resolve_output_dir(args.input, run.output, batch=False)
        state = None if args.overwrite else archive_state(output, args.input.stem, options)
        if state is not None:
            how = "with other settings" if state == ARCHIVE_OUTDATED else "already"
            logger.info(
                "%s is %s archived in %s; use --overwrite to redo it", args.input, how, output
            )
            return EXIT_OK
        process_video(args.input, output, options)
        return EXIT_OK
    if not args.input.is_dir():
        raise UsageError(f"Input not found: {args.input}")

    videos = find_videos(args.input, recursive=run.recursive, skip_dir=is_output_dir)
    if not videos:
        logger.warning("No videos found in %s", args.input)
        return EXIT_OK
    workers = run.workers or default_workers()
    logger.info("Found %d videos; up to %d processed in parallel", len(videos), workers)

    def progress(done: int, total: int, result: BatchResult) -> None:
        status = "ok" if result.ok else "FAILED"
        logger.info("[%d/%d] %s: %s", done, total, result.item.name, status)

    results = process_batch(
        videos,
        run.output,
        options,
        source=args.input,
        skip_complete=not args.overwrite,
        workers=workers,
        on_progress=progress,
    )
    failed = [r for r in results if not r.ok]
    skipped = sum(r.skipped for r in results)
    outdated = sum(r.detail == ARCHIVE_OUTDATED for r in results)
    archived = len(results) - len(failed) - skipped
    summary = f"Done: {archived} archived, {skipped - outdated} already archived"
    if outdated:
        summary += f", {outdated} archived with other settings (not redone)"
    summary += f", {len(failed)} failed"
    if len(results) < len(videos):
        summary += f", {len(videos) - len(results)} not started"
    logger.info(summary)
    if skipped:
        logger.info("Use --overwrite to archive the skipped videos again")
    for result in failed:
        logger.error("Failed: %s (%s)", result.item, result.error)
    return EXIT_FAILED if failed else EXIT_OK


def config_target(args: argparse.Namespace) -> Path:
    """The file to create: --config, then $VIDEOSEMANTICREDUCER_CONFIG, then the default."""
    if getattr(args, "config", None) is not None:
        return args.config
    if env := os.environ.get(CONFIG_ENV):
        return Path(env)
    return default_config_path()


def cmd_config_init(args: argparse.Namespace) -> int:
    path = config_target(args)
    if path.exists() and not args.force:
        raise ConfigError(f"{path} already exists; use --force to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_template(), encoding="utf-8")
    print(f"Config file written to {path}")
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    path = find_config(args.config)
    defaults = load_config(path)  # before printing anything, so errors come alone
    print(f"# Source: {path or 'built-in defaults (no config file found)'}")
    print(render_template(defaults), end="")
    return 0


def cmd_config_path(args: argparse.Namespace) -> int:
    path = config_target(args)
    status = "exists" if path.is_file() else "not created yet; run `config init`"
    print(f"{path} ({status})")
    return 0


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
