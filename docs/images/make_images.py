"""Make the images of the main README from a synthetic video.

    uv run python docs/images/make_images.py

Draws a 2-minute landscape going from dawn to night (no real footage, so the
images can be published), then runs the tools on it: the contact sheet and the
terminal output in the README are real outputs.
"""

from __future__ import annotations

import colorsys
import io
import math
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rich.console import Console
from rich.text import Text

HERE = Path(__file__).parent
WIDTH, HEIGHT, FPS, SECONDS = 1280, 720, 24, 120
TOOLS = Path(sys.executable).parent


def sky(t: float) -> tuple[np.ndarray, tuple[int, int, int]]:
    """Top and bottom colours of the sky at `t` (0 dawn .. 1 night), and the hills' colour."""
    stops = [  # (t, top, horizon, hills)
        (0.00, (40, 30, 80), (250, 150, 110), (60, 50, 80)),
        (0.25, (60, 140, 230), (180, 220, 250), (60, 120, 70)),
        (0.55, (50, 120, 220), (200, 230, 250), (70, 130, 60)),
        (0.75, (90, 60, 140), (255, 140, 60), (90, 60, 60)),
        (1.00, (10, 12, 40), (40, 40, 90), (15, 20, 35)),
    ]
    for (t0, *a), (t1, *b) in zip(stops, stops[1:], strict=False):
        if t <= t1:
            k = (t - t0) / (t1 - t0)
            top, low, hills = (
                tuple(round(x + (y - x) * k) for x, y in zip(ca, cb, strict=True))
                for ca, cb in zip(a, b, strict=True)
            )
            rows = np.linspace(0, 1, HEIGHT)[:, None]
            gradient = np.array(top) * (1 - rows) + np.array(low) * rows
            return np.repeat(gradient[:, None, :], WIDTH, axis=1).astype(np.uint8), hills
    raise ValueError(t)


def hill_line(x: np.ndarray, base: float, amp: float, phase: float) -> np.ndarray:
    return HEIGHT * base - amp * (np.sin(x / 140 + phase) + 0.5 * np.sin(x / 57 + 2 * phase))


def frame(index: int, font: ImageFont.FreeTypeFont) -> Image.Image:
    t = index / (FPS * SECONDS)
    pixels, hills = sky(t)
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    # The sun (then the moon) crosses the sky.
    angle = math.pi * (t * 1.25 if t < 0.8 else (t - 0.8) * 4)
    cx, cy = WIDTH * (0.1 + 0.8 * angle / math.pi), HEIGHT * (0.75 - 0.55 * math.sin(angle))
    color = (255, 230, 150) if t < 0.8 else (230, 235, 255)
    draw.ellipse((cx - 45, cy - 45, cx + 45, cy + 45), fill=color)
    if t > 0.85:  # stars
        rng = np.random.default_rng(7)
        for sx, sy in rng.integers(0, (WIDTH, HEIGHT // 2), size=(80, 2)):
            draw.point((int(sx), int(sy)), fill=(255, 255, 255))
    # Three layers of hills scrolling at different speeds.
    x = np.arange(WIDTH + 1, dtype=float)
    for depth, (base, amp, speed) in enumerate([(0.72, 40, 0.4), (0.82, 55, 1.0), (0.93, 35, 2.2)]):
        shade = 1 - 0.25 * (2 - depth)
        fill = tuple(round(c * shade) for c in hills)
        line = hill_line(x + index * speed, base, amp, depth * 1.7)
        draw.polygon([(0, HEIGHT), *zip(x, line, strict=True), (WIDTH, HEIGHT)], fill=fill)
    # A small sail boat on the front hill, so frames differ even where the sky does not.
    bx = (index * 3) % (WIDTH + 200) - 100
    by = hill_line(np.array([bx + index * 2.2]), 0.93, 35, 3.4)[0] - 10
    hue = colorsys.hsv_to_rgb((t * 3) % 1, 0.6, 0.95)
    draw.polygon(
        [(bx, by), (bx + 40, by), (bx + 10, by - 60)], fill=tuple(round(c * 255) for c in hue)
    )
    seconds = index // FPS
    draw.text((40, 30), f"{seconds // 60:02d}:{seconds % 60:02d}", font=font, fill=(255, 255, 255))
    return image


def make_video(path: Path) -> None:
    font = (
        ImageFont.truetype("arial.ttf", 48)
        if sys.platform == "win32"
        else ImageFont.load_default(48)
    )
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        stream.time_base = Fraction(1, FPS)
        stream.options = {"crf": "26", "preset": "veryfast", "g": str(FPS * 2)}
        for index in range(FPS * SECONDS):
            video_frame = av.VideoFrame.from_image(frame(index, font))
            video_frame.pts = index
            container.mux(stream.encode(video_frame))
        container.mux(stream.encode())


def tool(*args: str) -> str:
    """Run a tool installed next to this Python, returning what it printed."""
    result = subprocess.run(
        [str(TOOLS / args[0]), *args[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout + result.stderr


def terminal_svg(commands: list[tuple[str, str]], path: Path) -> None:
    """An SVG of a terminal showing each command and what it printed."""
    console = Console(record=True, width=88, file=io.StringIO())
    for command, output in commands:
        console.print(Text("> ", style="bold green") + Text(command, style="bold"))
        for line in output.rstrip().splitlines():
            style = "green" if line.endswith(": OK") or line.startswith("READY") else ""
            console.print(Text(line, style=style))
        console.print()
    console.save_svg(str(path), title="vtools")


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="vt-readme-"))
    try:
        videos = work / "Videos"
        videos.mkdir()
        demo = videos / "landscape.mp4"
        make_video(demo)
        sheet = tool("vthumb", "sheet", str(demo), "-o", str(work / "sheet.jpg"), "--no-progress")
        Image.open(work / "sheet.jpg").save(HERE / "contact-sheet.jpg", quality=85)
        for name in ("hills", "dusk"):
            shutil.copy(demo, videos / f"{name}.mp4")
        archive = tool("vsr", "archive", str(videos), "-w", "2", "--no-progress")
        status = tool("vsr", "status", str(videos))
        terminal_svg(
            [
                (
                    "vthumb landscape.mp4",
                    sheet.replace(str(work / "sheet.jpg"), "landscape_sheet.jpg"),
                ),
                ("vsr archive .\\Videos", archive.replace(str(videos) + "\\", "")),
                ("vsr status .\\Videos", status.replace(str(videos) + "\\", "")),
            ],
            HERE / "terminal.svg",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
