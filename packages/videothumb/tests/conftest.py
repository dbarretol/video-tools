"""Tiny synthetic media files, generated once per session with PyAV.

Frame i is a flat gray of level i * GRAY_STEP, so tests can tell which frame
ended up in an output image.
"""

from __future__ import annotations

from pathlib import Path

import av
import pytest
from PIL import Image

FPS = 10
FRAMES = 30
GOP = 10
GRAY_STEP = 8
WIDTH, HEIGHT = 160, 90


def write_clip(path: Path) -> Path:
    with av.open(str(path), mode="w") as out:
        video = out.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        video.options = {"crf": "1", "g": str(GOP), "keyint_min": str(GOP), "sc_threshold": "0"}
        for index in range(FRAMES):
            image = Image.new("RGB", (WIDTH, HEIGHT), (index * GRAY_STEP,) * 3)
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            out.mux(video.encode(frame))
        out.mux(video.encode())
    return path


def write_audio_only(path: Path) -> Path:
    with av.open(str(path), mode="w") as out:
        audio = out.add_stream("aac", rate=48000)
        audio.layout = "mono"
        for pts in range(0, 48000, 1024):
            frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
            frame.planes[0].update(bytes(1024 * 4))
            frame.sample_rate, frame.pts = 48000, pts
            out.mux(audio.encode(frame))
        out.mux(audio.encode())
    return path


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("media") / "clip.mp4")


@pytest.fixture(scope="session")
def audio_only(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_audio_only(tmp_path_factory.mktemp("media") / "audio.m4a")


@pytest.fixture
def folder(clip: Path, tmp_path: Path) -> Path:
    """Two copies of the clip, a broken video and a non-video file."""
    videos = tmp_path / "videos"
    videos.mkdir()
    for name in ("a.mp4", "b.mkv"):
        (videos / name).write_bytes(clip.read_bytes())
    (videos / "broken.mp4").write_bytes(b"not a video")
    (videos / "notes.txt").write_text("not a video")
    return videos


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep logs and settings out of the user's profile."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture(scope="session")
def gray_index():
    """Index of the synthetic frame shown at point `xy` (default: center) of an image."""

    def index(image: Image.Image, xy: tuple[int, int] | None = None) -> int:
        level = image.convert("L").getpixel(xy or (image.width // 2, image.height // 2))
        return round(level / GRAY_STEP)

    return index
