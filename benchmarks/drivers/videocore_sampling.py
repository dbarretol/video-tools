"""Benchmark driver: videocore frame sampling with the workloads of both applications.

Each phase mirrors one sampling job of videothumb or videosemanticreducer (same frame
count, timestamps and output size), so results can be compared phase by phase.

Usage: python videocore_sampling.py <video> <output_dir>
"""

from __future__ import annotations

import json
import sys
import time

t = time.perf_counter()
from videocore import evenly_spaced, probe, sample_frames  # noqa: E402

phases: dict[str, float] = {"import": time.perf_counter() - t}

video = sys.argv[1]

t = time.perf_counter()
info = probe(video)
phases["probe"] = time.perf_counter() - t

d = info.duration
aspect = info.video.height / info.video.width


def scaled(width: int) -> tuple[int, int]:
    return width, round(width * aspect / 2) * 2


workloads = [
    ("thumb_sheet_20_keyframe", evenly_spaced(0, d, 20, endpoints=False), "keyframe", scaled(320)),
    (
        "vsr_snapshots_150_keyframe",
        evenly_spaced(0.01 * d, 0.99 * d, 150),
        "keyframe",
        scaled(1280),
    ),
    ("vsr_grid_144_keyframe", evenly_spaced(0.01 * d, 0.99 * d, 144), "keyframe", scaled(240)),
    ("vsr_global_64_precise", evenly_spaced(0.01 * d, 0.99 * d, 64), "precise", (144, 144)),
    (
        "vsr_keyframes_22_precise",
        [
            d * pct / 100
            for pct in (
                0.5,
                2,
                5,
                10,
                15,
                20,
                25,
                30,
                35,
                40,
                45,
                50,
                55,
                60,
                65,
                70,
                75,
                80,
                85,
                90,
                95,
                98,
            )
        ],
        "precise",
        (256, 256),
    ),
]

for name, timestamps, mode, size in workloads:
    t = time.perf_counter()
    frames = sample_frames(video, timestamps, mode=mode, size=size)
    phases[name] = time.perf_counter() - t
    missing = sum(f is None for f in frames)
    if missing:
        print(f"warning: {name} missed {missing}/{len(frames)} frames", file=sys.stderr)

print(json.dumps({"phases": phases}))
