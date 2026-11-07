"""Synthetic test videos, generated once per session with PyAV.

Every frame is a flat gray whose level encodes its index (index * gray_step), so
tests can tell exactly which frame a sampler returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av
import pytest
from PIL import Image


@dataclass(frozen=True)
class ClipSpec:
    fps: int = 10
    frames: int = 20
    gop: int = 5
    gray_step: int = 12
    width: int = 160
    height: int = 120

    def gray_for(self, index: int) -> int:
        return index * self.gray_step


SPEC = ClipSpec()


def write_video(path: Path, *, with_audio: bool) -> Path:
    with av.open(str(path), mode="w") as out:
        out.metadata["title"] = "videocore test clip"
        video = out.add_stream("libx264", rate=SPEC.fps)
        video.width, video.height, video.pix_fmt = SPEC.width, SPEC.height, "yuv420p"
        video.options = {
            "crf": "1",
            "g": str(SPEC.gop),
            "keyint_min": str(SPEC.gop),
            "sc_threshold": "0",
            "bf": "0",
        }

        audio = None
        if with_audio:
            audio = out.add_stream("aac", rate=48000)
            audio.layout = "mono"

        for index in range(SPEC.frames):
            image = Image.new("RGB", (SPEC.width, SPEC.height), (SPEC.gray_for(index),) * 3)
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            out.mux(video.encode(frame))
        out.mux(video.encode())

        if audio is not None:
            samples_per_frame = 1024
            total = int(48000 * SPEC.frames / SPEC.fps)
            for pts in range(0, total, samples_per_frame):
                frame = av.AudioFrame(format="fltp", layout="mono", samples=samples_per_frame)
                frame.planes[0].update(bytes(samples_per_frame * 4))
                frame.sample_rate = 48000
                frame.pts = pts
                out.mux(audio.encode(frame))
            out.mux(audio.encode())
    return path


@pytest.fixture(scope="session")
def spec() -> ClipSpec:
    return SPEC


@pytest.fixture(scope="session")
def video_with_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_video(tmp_path_factory.mktemp("videos") / "with_audio.mp4", with_audio=True)


@pytest.fixture(scope="session")
def video_only(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_video(tmp_path_factory.mktemp("videos") / "video_only.mkv", with_audio=False)
