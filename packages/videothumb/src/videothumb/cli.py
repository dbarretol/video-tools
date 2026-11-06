"""Entry point: without arguments opens the GUI, with arguments runs the command line."""

from __future__ import annotations

import argparse
import logging
import sys
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
)
from videocore.options import add_arguments, given_values, options_of
from videothumb import __version__
from videothumb.common import APP_NAME, Mode
from videothumb.jobs import Options, default_output, has_output, process, process_folder
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

logger = logging.getLogger(__name__)


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value}")
    return value


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
            "receives every output, recreating subfolders with -r (default: next to each video)"
        ),
    )

    add_arguments(parser, SheetOptions, SnapshotOptions)
    parser.add_argument(
        "--precise",
        action="store_true",
        help="frames at exact positions (slower); by default the nearest earlier keyframe is used",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="include subfolders")
    parser.add_argument(
        "-w",
        "--workers",
        type=positive_int,
        help=f"videos processed in parallel with --folder (default: {default_workers()})",
    )
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
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from videothumb.gui.app import launch

        launch()
        return 0

    args = build_parser().parse_args(argv)  # exits with 2 on invalid arguments
    configure_logging(APP_NAME, level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        mode, options = Mode(args.mode), build_options(args)
        if args.video is not None:
            if not args.video.is_file():
                raise UsageError(f"Video not found: {args.video}")
            return run_single(args.video, mode, options, args.output, args.skip_existing)
        if not args.folder.is_dir():
            raise UsageError(f"Folder not found: {args.folder}")
        return run_batch(args, mode, options)
    except KeyboardInterrupt:
        logger.info("Interrupted", exc_info=True)
        print("Interrupted; unfinished files were removed.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except UsageError as exc:
        logger.error("Usage error: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error("Failed: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED


MODE_OPTIONS: dict[Mode, type[SheetOptions] | type[SnapshotOptions]] = {
    Mode.SHEET: SheetOptions,
    Mode.SNAPSHOTS: SnapshotOptions,
}


def build_options(args: argparse.Namespace) -> Options:
    """The options of the selected mode; warns about flags that only apply to another one."""
    mode = Mode(args.mode)
    cls = MODE_OPTIONS[mode]
    given = given_values(cls, args)
    for other in MODE_OPTIONS.values():
        for opt in options_of(other):
            if opt.name not in given and getattr(args, opt.name, None) is not None:
                logger.warning("%s does not apply to --mode %s; ignored", opt.flags[-1], mode)
    sampling = "precise" if args.precise else "keyframe"
    return cls(**given, sampling=sampling)


def run_single(
    video: Path, mode: Mode, options: Options, output: Path | None, skip_existing: bool
) -> int:
    target = output or default_output(video, mode)
    if skip_existing and has_output(target, mode):
        print(f"Skipped: {target} already exists (use --overwrite to replace it)")
        return EXIT_OK
    result = process(video, mode, options, output)
    if mode is Mode.SHEET:
        print(f"Contact sheet saved to {result}")
    else:
        print(f"Snapshots saved to {result}")
    return EXIT_OK


def run_batch(args: argparse.Namespace, mode: Mode, options: Options) -> int:
    videos = find_videos(args.folder, recursive=args.recursive)
    if not videos:
        print(f"No videos found in {args.folder}")
        return EXIT_OK

    def progress(done: int, total: int, result: BatchResult) -> None:
        status = "ok" if result.ok else "FAILED"
        print(f"[{done}/{total}] {result.item.name}: {status}")

    results = process_folder(
        videos,
        mode,
        options,
        root=args.output,
        source=args.folder,
        skip_existing=args.skip_existing,
        workers=args.workers,
        on_progress=progress,
    )
    failed = [r for r in results if not r.ok]
    skipped = sum(r.skipped for r in results)
    succeeded = len(results) - len(failed) - skipped
    summary = f"Done: {succeeded} succeeded, {len(failed)} failed"
    if skipped:
        summary += f", {skipped} skipped (output already exists)"
    print(summary + ".")
    for result in failed:
        print(f"  - {result.item.name}: {result.error}", file=sys.stderr)
    return EXIT_FAILED if failed else EXIT_OK
