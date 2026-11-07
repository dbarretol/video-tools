"""Check a built folder of executables on a generated clip: every program starts and works.

    uv run python packaging/smoke_test.py build/dist/video-tools

Runs the executables as a user would, from a clean folder, with two videos and two workers so
the frozen worker processes are exercised too. Exits non-zero at the first failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image

SECONDS = 8
FPS = 10


def write_clip(path: Path, seconds: int = SECONDS) -> Path:
    """A small H.264 clip whose gray level changes every frame, with a keyframe every second."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        stream.codec_context.gop_size = FPS
        stream.codec_context.time_base = Fraction(1, FPS)
        for index in range(seconds * FPS):
            image = Image.new("RGB", (320, 240), (index * 3 % 256,) * 3)
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            container.mux(stream.encode(frame))
        container.mux(stream.encode(None))
    return path


def run(program: Path, *args: str) -> subprocess.CompletedProcess[str]:
    started = time.perf_counter()
    result = subprocess.run(
        [str(program), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300
    )
    elapsed = time.perf_counter() - started
    print(f"{program.name} {' '.join(args)} -> {result.returncode} ({elapsed:.1f} s)")
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n")
        sys.exit(f"failed: {program.name} {' '.join(args)}")
    return result


def main() -> None:
    folder = Path(sys.argv[1]).resolve()
    suffix = ".exe" if sys.platform == "win32" else ""
    vtools, vthumb, vsr = (folder / f"{name}{suffix}" for name in ("vtools", "vthumb", "vsr"))

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        videos = work / "videos"
        videos.mkdir()
        write_clip(videos / "one.mp4")
        write_clip(videos / "two.mp4", seconds=SECONDS + 2)
        out = work / "out"

        for program in (vtools, vthumb, vsr):
            version = run(program, "--version").stdout
            assert version.strip(), f"{program.name} --version printed nothing"
        assert "Contact sheets" in run(vtools, "--help").stdout

        run(vthumb, "sheet", str(videos), "-o", str(out / "sheets"), "-w", "2", "--no-progress")
        assert len(list((out / "sheets").glob("*_sheet.jpg"))) == 2

        run(vtools, "pack", str(videos / "one.mp4"), "-o", str(out / "one.zip"), "--no-progress")
        assert (out / "one.zip").stat().st_size > 0

        report = run(vsr, "archive", str(videos), "-o", str(out / "archive"), "-w", "2", "--json")
        assert json.loads(report.stdout)["exit_code"] == 0
        status = run(vtools, "status", str(videos), "-o", str(out / "archive")).stdout
        assert "2 ready to delete" in status, status

    print("smoke test passed")


if __name__ == "__main__":
    main()
