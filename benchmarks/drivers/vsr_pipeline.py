"""Benchmark driver: the full videosemanticreducer pipeline on one video.

Runs ``process_video`` with default options and reports its per-step timings, so
the total can be compared with the original tool's ``process`` command.

Usage: python vsr_pipeline.py <video> <output_dir>
"""

from __future__ import annotations

import json
import sys
import time

t = time.perf_counter()
from videosemanticreducer.pipeline import process_video  # noqa: E402

import_time = time.perf_counter() - t

result = process_video(sys.argv[1], sys.argv[2])
print(json.dumps({"phases": {"import": import_time, **result.timings}}))
