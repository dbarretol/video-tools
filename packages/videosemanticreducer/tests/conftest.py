"""Tiny synthetic media files, generated once per session with PyAV.

The video shows a square moving over a background whose color changes over
time, so perceptual hashes differ from frame to frame.
"""

from __future__ import annotations

from pathlib import Path

import av
import pytest
from PIL import Image, ImageDraw

FPS = 10
FRAMES = 40
WIDTH, HEIGHT = 160, 90
AUDIO_RATE = 48000


def draw_frame(index: int) -> Image.Image:
    shade = index * 255 // FRAMES
    image = Image.new("RGB", (WIDTH, HEIGHT), (shade, 64, 255 - shade))
    x = index * (WIDTH - 30) // FRAMES
    ImageDraw.Draw(image).rectangle((x, 30, x + 30, 60), fill=(255, 255, 255))
    return image


def _add_audio(out: av.container.OutputContainer) -> av.AudioStream:
    audio = out.add_stream("aac", rate=AUDIO_RATE)
    audio.layout = "mono"
    return audio


def _write_silence(
    out: av.container.OutputContainer, audio: av.AudioStream, seconds: float
) -> None:
    samples = 1024
    for pts in range(0, int(AUDIO_RATE * seconds), samples):
        frame = av.AudioFrame(format="fltp", layout="mono", samples=samples)
        frame.planes[0].update(bytes(samples * 4))
        frame.sample_rate = AUDIO_RATE
        frame.pts = pts
        out.mux(audio.encode(frame))
    out.mux(audio.encode())


def write_clip(path: Path) -> Path:
    with av.open(str(path), mode="w") as out:
        out.metadata["title"] = "vsr test clip"
        video = out.add_stream("libx264", rate=FPS)
        video.width, video.height, video.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        video.options = {"crf": "18", "g": "10"}
        audio = _add_audio(out)  # every stream must exist before muxing starts
        for index in range(FRAMES):
            frame = av.VideoFrame.from_image(draw_frame(index))
            frame.pts = index
            out.mux(video.encode(frame))
        out.mux(video.encode())
        _write_silence(out, audio, FRAMES / FPS)
    return path


def write_audio_only(path: Path) -> Path:
    with av.open(str(path), mode="w") as out:
        _write_silence(out, _add_audio(out), 2.0)
    return path


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_clip(tmp_path_factory.mktemp("media") / "clip.mp4")


@pytest.fixture(scope="session")
def audio_only(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_audio_only(tmp_path_factory.mktemp("media") / "audio.m4a")


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep log files written by the CLI out of the user's profile."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
