"""Entry point: without arguments opens the GUI, with arguments runs the command line."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from videocore import BatchResult, configure_logging, default_workers, find_videos
from videothumb import __version__
from videothumb.common import APP_NAME, Mode
from videothumb.jobs import Options, process, process_folder
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "Generate contact sheets and individual snapshots from videos. "
            "Run without arguments to open the desktop app."
        ),
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
            "receives every output (default: next to each video)"
        ),
    )

    sheet = parser.add_argument_group("contact sheet")
    sheet.add_argument("--columns", type=int, default=5, help="default: 5")
    sheet.add_argument("--rows", type=int, default=4, help="default: 4")
    sheet.add_argument("--thumbnail-width", type=int, default=320, help="pixels (default: 320)")

    snapshots = parser.add_argument_group("snapshots")
    snapshots.add_argument("--count", type=int, default=10, help="default: 10")

    parser.add_argument("--quality", type=int, default=90, help="JPEG quality 1-100 (default: 90)")
    parser.add_argument(
        "--precise",
        action="store_true",
        help="frames at exact positions (slower); by default the nearest earlier keyframe is used",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        help=f"videos processed in parallel with --folder (default: {default_workers()})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from videothumb.gui.app import launch

        launch()
        return 0

    args = build_parser().parse_args(argv)
    configure_logging(APP_NAME, level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        mode, options = Mode(args.mode), build_options(args)
        if args.video is not None:
            return run_single(args.video, mode, options, args.output)
        return run_batch(args, mode, options)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("Failed: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def build_options(args: argparse.Namespace) -> Options:
    sampling = "precise" if args.precise else "keyframe"
    if args.mode == Mode.SHEET:
        return SheetOptions(
            columns=args.columns,
            rows=args.rows,
            thumbnail_width=args.thumbnail_width,
            jpeg_quality=args.quality,
            sampling=sampling,
        )
    return SnapshotOptions(count=args.count, jpeg_quality=args.quality, sampling=sampling)


def run_single(video: Path, mode: Mode, options: Options, output: Path | None) -> int:
    result = process(video, mode, options, output)
    if mode is Mode.SHEET:
        print(f"Contact sheet saved to {result}")
    else:
        print(f"Snapshots saved to {result}")
    return 0


def run_batch(args: argparse.Namespace, mode: Mode, options: Options) -> int:
    videos = find_videos(args.folder, recursive=args.recursive)
    if not videos:
        print(f"No videos found in {args.folder}")
        return 0

    def progress(done: int, total: int, result: BatchResult) -> None:
        status = "ok" if result.ok else "FAILED"
        print(f"[{done}/{total}] {result.item.name}: {status}")

    results = process_folder(
        videos, mode, options, root=args.output, workers=args.workers, on_progress=progress
    )
    failed = [r for r in results if not r.ok]
    print(f"Done: {len(results) - len(failed)} succeeded, {len(failed)} failed.")
    for result in failed:
        print(f"  - {result.item.name}: {result.error}", file=sys.stderr)
    return 1 if failed else 0
