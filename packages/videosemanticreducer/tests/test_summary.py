import av
import pytest
from PIL import Image

from videosemanticreducer.summary import SummaryWriter, even_size


def test_even_size_scales_down_to_max_side():
    assert even_size(1920, 1080, 1280) == (1280, 720)
    assert even_size(1080, 1920, 1280) == (720, 1280)


def test_even_size_never_upscales_and_rounds_to_even():
    assert even_size(640, 360, 1280) == (640, 360)
    assert even_size(642, 362, 1280) == (642, 362)
    assert all(side % 2 == 0 for side in even_size(643, 365, 1280))


def read_video(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        frames = list(container.decode(stream))
        return float(stream.average_rate), frames, container.duration / av.time_base


def test_each_still_lasts_still_duration(tmp_path):
    path = tmp_path / "summary.mp4"
    with SummaryWriter(path, (64, 48), still_duration=0.4) as writer:
        writer.add(Image.new("RGB", (64, 48), "navy"), duration=2.0)
        for shade in (0, 128, 255):
            writer.add(Image.new("RGB", (64, 48), (shade,) * 3))

    rate, frames, duration = read_video(path)
    assert writer.frames_written == 5 + 3
    assert len(frames) == 8
    assert rate == pytest.approx(2.5)
    assert duration == pytest.approx(8 * 0.4, abs=0.05)
    assert (frames[0].width, frames[0].height) == (64, 48)


def test_images_of_another_size_are_resized(tmp_path):
    path = tmp_path / "summary.mp4"
    with SummaryWriter(path, (64, 48), still_duration=0.5) as writer:
        writer.add(Image.new("RGB", (200, 100), "red"))
    _, frames, _ = read_video(path)
    assert (frames[0].width, frames[0].height) == (64, 48)


def test_failed_encode_removes_the_partial_file(tmp_path):
    path = tmp_path / "summary.mp4"
    with pytest.raises(RuntimeError), SummaryWriter(path, (64, 48), still_duration=0.4) as writer:
        writer.add(Image.new("RGB", (64, 48)))
        raise RuntimeError("decoding failed")
    assert not path.exists()


def test_still_duration_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        SummaryWriter(tmp_path / "x.mp4", (64, 48), still_duration=0)
