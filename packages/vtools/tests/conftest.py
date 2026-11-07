"""A tiny synthetic clip, generated once per session with PyAV."""

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


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("media") / "clip.mp4")


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep logs, settings, state and config out of the user's profile."""
    for name in ("LOCALAPPDATA", "XDG_STATE_HOME", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.delenv("VIDEOTHUMB_CONFIG", raising=False)
    monkeypatch.delenv("VIDEOSEMANTICREDUCER_CONFIG", raising=False)
