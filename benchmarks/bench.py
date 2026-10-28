"""Time benchmark drivers against a set of videos.

A *target* is a driver script run with a given Python interpreter. The driver is
invoked as::

    <python> <driver> <video> <output_dir> [extra args...]

and must print, as the last line of stdout, a JSON object of the form
``{"phases": {"<phase name>": <seconds>, ...}}``. The runner adds the wall time of
the whole process (interpreter start-up and imports included) as ``process_total``.

Targets are declared in a TOML file::

    [[target]]
    name = "my-target"
    python = "%LOCALAPPDATA%/envs/foo/Scripts/python.exe"   # env vars and ~ are expanded
    driver = "drivers/foo.py"                                # relative to the TOML file
    args = ["--mode", "sheet"]                               # optional

Usage:
    uv run python benchmarks/bench.py --targets targets.toml --video a.mkv [--video b.mp4]
        [--repeat 3] [--warmup 1] [--only NAME ...] [--results DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_RESULTS = Path(__file__).parent / "results"


@dataclass
class Target:
    name: str
    python: Path
    driver: Path
    args: list[str] = field(default_factory=list)


@dataclass
class Run:
    target: str
    video: str
    phases: dict[str, float]


def expand(value: str, base: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else (base / path).resolve()


def load_targets(path: Path) -> list[Target]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    return [
        Target(
            name=entry["name"],
            python=expand(entry["python"], base),
            driver=expand(entry["driver"], base),
            args=list(entry.get("args", [])),
        )
        for entry in data.get("target", [])
    ]


def run_once(target: Target, video: Path) -> dict[str, float]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="bench_") as out_dir:
        cmd = [str(target.python), str(target.driver), str(video), out_dir, *target.args]
        start = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        elapsed = time.perf_counter() - start

    if proc.returncode != 0:
        raise RuntimeError(
            f"{target.name} failed on {video.name} (exit {proc.returncode}):\n{proc.stderr[-2000:]}"
        )
    lines = proc.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"{target.name} printed no result on {video.name}")
    phases = json.loads(lines[-1])["phases"]
    phases["process_total"] = elapsed
    return phases


def summarize(runs: list[Run]) -> dict[tuple[str, str], dict[str, dict[str, float]]]:
    grouped: dict[tuple[str, str], dict[str, list[float]]] = {}
    for run in runs:
        bucket = grouped.setdefault((run.target, run.video), {})
        for phase, seconds in run.phases.items():
            bucket.setdefault(phase, []).append(seconds)
    return {
        key: {
            phase: {"median": statistics.median(values), "min": min(values), "n": len(values)}
            for phase, values in phases.items()
        }
        for key, phases in grouped.items()
    }


def render_markdown(summary, meta: dict) -> str:
    lines = [
        f"# Benchmark {meta['timestamp']}",
        "",
        f"- Machine: {meta['machine']}",
        f"- Python (runner): {meta['python']}",
        f"- Repeats: {meta['repeat']} (+{meta['warmup']} warm-up, discarded)",
        "",
    ]
    for (target, video), phases in summary.items():
        lines += [
            f"## {target} - {video}",
            "",
            "| Phase | Median (s) | Min (s) |",
            "|---|---:|---:|",
        ]
        for phase, stats in phases.items():
            lines.append(f"| {phase} | {stats['median']:.3f} | {stats['min']:.3f} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time benchmark drivers against videos.")
    parser.add_argument("--targets", type=Path, required=True, help="TOML file declaring targets")
    parser.add_argument("--video", type=Path, action="append", required=True, help="input video")
    parser.add_argument("--repeat", type=int, default=3, help="measured runs per target/video")
    parser.add_argument("--warmup", type=int, default=1, help="discarded runs before measuring")
    parser.add_argument("--only", nargs="+", help="run only these target names")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS, help="output directory")
    args = parser.parse_args(argv)

    targets = [t for t in load_targets(args.targets) if not args.only or t.name in args.only]
    if not targets:
        print("error: no targets selected", file=sys.stderr)
        return 1
    for video in args.video:
        if not video.is_file():
            print(f"error: video not found: {video}", file=sys.stderr)
            return 1

    runs: list[Run] = []
    for target in targets:
        for video in args.video:
            for i in range(args.warmup + args.repeat):
                measured = i >= args.warmup
                label = f"run {i - args.warmup + 1}/{args.repeat}" if measured else "warm-up"
                print(f"{target.name} | {video.name} | {label} ...", flush=True)
                phases = run_once(target, video)
                print(f"  process_total {phases['process_total']:.2f}s", flush=True)
                if measured:
                    runs.append(Run(target.name, video.name, phases))

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    meta = {
        "timestamp": timestamp,
        "machine": f"{platform.processor() or platform.machine()} ({os.cpu_count()} logical CPUs)",
        "python": platform.python_version(),
        "repeat": args.repeat,
        "warmup": args.warmup,
    }
    summary = summarize(runs)

    args.results.mkdir(parents=True, exist_ok=True)
    stem = args.results / f"bench_{timestamp}"
    stem.with_suffix(".json").write_text(
        json.dumps(
            {
                "meta": meta,
                "runs": [run.__dict__ for run in runs],
                "summary": [
                    {"target": t, "video": v, "phases": p} for (t, v), p in summary.items()
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown = render_markdown(summary, meta)
    stem.with_suffix(".md").write_text(markdown, encoding="utf-8")
    print()
    print(markdown)
    print(f"results written to {stem}.json and {stem}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
