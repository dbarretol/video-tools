"""Command-line entry point for videosemanticreducer."""

from __future__ import annotations

import argparse
import dataclasses
import functools
import logging
from pathlib import Path

from videocore import (
    BatchResult,
    configure_logging,
    default_workers,
    find_videos,
    probe,
    run_parallel,
)
from videosemanticreducer import __version__
from videosemanticreducer.compare import Comparison, compare_fingerprints
from videosemanticreducer.fingerprint import compute_fingerprint, load_fingerprint, save_fingerprint
from videosemanticreducer.pipeline import ProcessOptions, process_video, resolve_output_dir

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

    process = commands.add_parser("process", help="archive a video or every video in a folder")
    process.add_argument("input", type=Path, help="video file or folder")
    process.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output folder (default: <stem>_summary next to each video)",
    )
    process.add_argument("-n", "--num-snapshots", type=int, default=150, help="default: 150")
    process.add_argument(
        "-d",
        "--snap-duration",
        type=float,
        default=0.4,
        help="seconds each snapshot is shown in the summary (default: 0.4)",
    )
    process.add_argument("--thumb-cols", type=int, default=12, help="grid columns (default: 12)")
    process.add_argument("--thumb-rows", type=int, default=12, help="grid rows (default: 12)")
    process.add_argument("--no-video", action="store_true", help="skip the summary video")
    process.add_argument("--no-thumbnail", action="store_true", help="skip the thumbnail grid")
    process.add_argument("--no-fingerprint", action="store_true", help="skip the fingerprint")
    process.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    process.add_argument(
        "-w",
        "--workers",
        type=int,
        help=f"videos processed in parallel for folders (default: {default_workers()})",
    )
    process.set_defaults(handler=cmd_process)

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


def cmd_process(args: argparse.Namespace) -> int:
    options = ProcessOptions(
        num_snapshots=args.num_snapshots,
        snap_duration=args.snap_duration,
        thumb_cols=args.thumb_cols,
        thumb_rows=args.thumb_rows,
        summary=not args.no_video,
        thumbnail=not args.no_thumbnail,
        fingerprint=not args.no_fingerprint,
    )
    if args.input.is_file():
        process_video(args.input, resolve_output_dir(args.input, args.output, batch=False), options)
        return 0
    if not args.input.is_dir():
        raise FileNotFoundError(f"Input not found: {args.input}")

    videos = find_videos(args.input, recursive=args.recursive)
    if not videos:
        logger.warning("No videos found in %s", args.input)
        return 0
    workers = args.workers or default_workers()
    if workers > 1:
        # Videos already run in parallel; one decoder per video avoids oversubscription.
        options = dataclasses.replace(options, sampling_workers=1)
    logger.info("Processing %d videos with %d workers", len(videos), workers)

    task = functools.partial(_process_in_batch, output=args.output, options=options)
    results = run_batch(task, videos, workers)
    failed = [r for r in results if not r.ok]
    logger.info("Done: %d/%d videos archived", len(results) - len(failed), len(results))
    for result in failed:
        logger.error("Failed: %s (%s)", result.item, result.error)
    return 1 if failed else 0


def run_batch(task: functools.partial, videos: list[Path], workers: int) -> list[BatchResult]:
    def progress(done: int, total: int, result: BatchResult) -> None:
        status = "ok" if result.ok else "FAILED"
        logger.info("[%d/%d] %s: %s", done, total, result.item.name, status)

    return run_parallel(task, videos, workers=workers, on_progress=progress)


def _process_in_batch(video: Path, *, output: Path | None, options: ProcessOptions) -> Path:
    # Module-level so it can be pickled into worker processes.
    out_dir = resolve_output_dir(video, output, batch=True)
    process_video(video, out_dir, options)
    return out_dir


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
