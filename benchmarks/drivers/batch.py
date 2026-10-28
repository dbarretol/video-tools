"""Benchmark driver: process a list of videos as one batch.

The "video" argument is a manifest: a text file with one video path per line
(blank lines and lines starting with # are ignored). Relative paths are
resolved against the manifest's folder.

Usage: python batch.py <manifest> <output_dir> --app {vsr,videothumb-sheet} [--workers N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def read_manifest(path: Path) -> list[Path]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        (path.parent / line.strip()).resolve()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--app", choices=["vsr", "videothumb-sheet"], required=True)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    videos = read_manifest(args.manifest)

    t = time.perf_counter()
    if args.app == "vsr":
        from videosemanticreducer.pipeline import process_batch

        def run():
            return process_batch(videos, args.output_dir, workers=args.workers)
    else:
        from videothumb.common import Mode
        from videothumb.jobs import process_folder
        from videothumb.sheet import SheetOptions

        def run():
            return process_folder(
                videos, Mode.SHEET, SheetOptions(), root=args.output_dir, workers=args.workers
            )

    phases: dict[str, float] = {"import": time.perf_counter() - t}

    t = time.perf_counter()
    results = run()
    phases[f"batch_{len(videos)}_videos"] = time.perf_counter() - t

    failed = [r for r in results if not r.ok]
    for result in failed:
        print(f"failed: {result.item}: {result.error}", file=sys.stderr)
    print(json.dumps({"phases": phases}))
    return 1 if failed else 0


# Worker processes are spawned and re-import this script, so it must only run when executed.
if __name__ == "__main__":
    sys.exit(main())
