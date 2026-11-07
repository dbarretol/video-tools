"""Benchmark driver: videothumb contact sheet, frames or pack for one video.

Mirrors the original tool's defaults (5x4 sheet of 320 px thumbnails, 10 frames).
Results before 0.2 name the frames phase ``snapshots_...``.

Usage: python videothumb_jobs.py <video> <output_dir> --mode {sheet,frames,pack}
       [--count N] [--precise]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("video")
parser.add_argument("output_dir")
parser.add_argument("--mode", choices=["sheet", "frames", "pack"], required=True)
parser.add_argument("--count", type=int, default=10)
parser.add_argument("--precise", action="store_true")
args = parser.parse_args()

t = time.perf_counter()
from videothumb.pack import PackOptions, make_pack  # noqa: E402
from videothumb.sheet import SheetOptions, make_sheet  # noqa: E402
from videothumb.snapshots import SnapshotOptions, make_snapshots  # noqa: E402

phases: dict[str, float] = {"import": time.perf_counter() - t}

out = Path(args.output_dir)
sampling = "precise" if args.precise else "keyframe"
t = time.perf_counter()
if args.mode == "sheet":
    make_sheet(args.video, out / "sheet.jpg", SheetOptions(sampling=sampling))
    phases[f"sheet_5x4_{sampling}"] = time.perf_counter() - t
elif args.mode == "pack":
    make_pack(args.video, out / "pack.zip", PackOptions(sampling=sampling))
    phases[f"pack_5x4_{sampling}"] = time.perf_counter() - t
else:
    make_snapshots(args.video, out / "frames", SnapshotOptions(count=args.count, sampling=sampling))
    phases[f"frames_{args.count}_{sampling}"] = time.perf_counter() - t

print(json.dumps({"phases": phases}))
