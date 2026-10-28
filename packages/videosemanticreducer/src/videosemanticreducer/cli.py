"""Command-line entry point for videosemanticreducer."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
from pathlib import Path

from videocore import BatchResult, configure_logging, default_workers, find_videos, probe
from videosemanticreducer import __version__
from videosemanticreducer.compare import Comparison, compare_fingerprints
from videosemanticreducer.config import (
    CONFIG_ENV,
    ConfigError,
    ProcessDefaults,
    default_config_path,
    find_config,
    load_config,
    render_template,
)
from videosemanticreducer.fingerprint import compute_fingerprint, load_fingerprint, save_fingerprint
from videosemanticreducer.pipeline import (
    ProcessOptions,
    process_batch,
    process_video,
    resolve_output_dir,
)

APP_NAME = "videosemanticreducer"
BUILTIN = ProcessDefaults()

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

    config_option = argparse.ArgumentParser(add_help=False)
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
    process.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output folder (default: <stem>_summary next to each video)",
    )
    process.add_argument(
        "-n", "--num-snapshots", type=int, help=f"default: {BUILTIN.num_snapshots}"
    )
    process.add_argument(
        "-d",
        "--snap-duration",
        type=float,
        help=f"seconds each snapshot is shown in the summary (default: {BUILTIN.snap_duration})",
    )
    process.add_argument("--thumb-cols", type=int, help=f"default: {BUILTIN.thumb_cols}")
    process.add_argument("--thumb-rows", type=int, help=f"default: {BUILTIN.thumb_rows}")
    toggle = argparse.BooleanOptionalAction
    process.add_argument(
        "--video", dest="summary", action=toggle, help="summary video (default: on)"
    )
    process.add_argument("--thumbnail", action=toggle, help="thumbnail grid (default: on)")
    process.add_argument("--fingerprint", action=toggle, help="fingerprint (default: on)")
    process.add_argument("-r", "--recursive", action=toggle, help="include subfolders")
    process.add_argument(
        "-w",
        "--workers",
        type=int,
        help=f"videos processed in parallel for folders (default: {default_workers()})",
    )
    process.set_defaults(handler=cmd_process)

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
    path = actions.add_parser("path", help="print where the config file is looked up")
    path.set_defaults(handler=cmd_config_path)

    fingerprint = commands.add_parser("fingerprint", help="only compute a video's fingerprint")
    fingerprint.add_argument("video", type=Path)
    fingerprint.add_argument(
        "-o", "--output", type=Path, help="JSON path (default: <video>.fingerprint.json)"
    )
    fingerprint.set_defaults(handler=cmd_fingerprint)

    compare = commands.add_parser("compare", help="compare two fingerprint files")
    compare.add_argument("fingerprint1", type=Path)
    compare.add_argument("fingerprint2", type=Path)
    compare.set_defaults(handler=cmd_compare)

    verify = commands.add_parser("verify", help="check a video against a saved fingerprint")
    verify.add_argument("video", type=Path)
    verify.add_argument("fingerprint", type=Path)
    verify.set_defaults(handler=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    level = logging.DEBUG if args.verbose else logging.INFO
    log_file = configure_logging(APP_NAME, level=level, console=True)
    logger.debug("Logging to %s", log_file)
    try:
        return args.handler(args)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        logger.debug("Details", exc_info=True)
        return 1


def process_settings(args: argparse.Namespace) -> ProcessDefaults:
    """Config file values, overridden by the flags given on the command line."""
    config_path = find_config(args.config)
    if config_path is not None:
        logger.info("Using defaults from %s", config_path)
    given = {
        field.name: getattr(args, field.name)
        for field in dataclasses.fields(ProcessDefaults)
        if getattr(args, field.name) is not None
    }
    return dataclasses.replace(load_config(config_path), **given)


def cmd_process(args: argparse.Namespace) -> int:
    settings = process_settings(args)
    options = ProcessOptions(
        num_snapshots=settings.num_snapshots,
        snap_duration=settings.snap_duration,
        thumb_cols=settings.thumb_cols,
        thumb_rows=settings.thumb_rows,
        summary=settings.summary,
        thumbnail=settings.thumbnail,
        fingerprint=settings.fingerprint,
    )
    if args.input.is_file():
        output = resolve_output_dir(args.input, settings.output, batch=False)
        process_video(args.input, output, options)
        return 0
    if not args.input.is_dir():
        raise FileNotFoundError(f"Input not found: {args.input}")

    videos = find_videos(args.input, recursive=settings.recursive)
    if not videos:
        logger.warning("No videos found in %s", args.input)
        return 0
    workers = settings.workers or default_workers()
    logger.info("Processing %d videos with %d workers", len(videos), workers)

    def progress(done: int, total: int, result: BatchResult) -> None:
        status = "ok" if result.ok else "FAILED"
        logger.info("[%d/%d] %s: %s", done, total, result.item.name, status)

    results = process_batch(videos, settings.output, options, workers=workers, on_progress=progress)
    failed = [r for r in results if not r.ok]
    logger.info("Done: %d/%d videos archived", len(results) - len(failed), len(results))
    for result in failed:
        logger.error("Failed: %s (%s)", result.item, result.error)
    return 1 if failed else 0


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
    print(f"# Source: {path or 'built-in defaults (no config file found)'}")
    print(render_template(load_config(path)), end="")
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
    fp1, fp2 = load_fingerprint(args.fingerprint1), load_fingerprint(args.fingerprint2)
    report = compare_fingerprints(fp1, fp2)
    print_comparison(
        "Fingerprint comparison",
        [("File 1", fp1.get("filename")), ("File 2", fp2.get("filename"))],
        report,
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    saved = load_fingerprint(args.fingerprint)
    logger.info("Computing fingerprint of %s", args.video)
    current = compute_fingerprint(probe(args.video))
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
    return 0


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
