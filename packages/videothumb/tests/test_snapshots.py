import re

import pytest
from PIL import Image

from videothumb.snapshots import (
    SnapshotOptions,
    default_snapshot_dir,
    frame_name,
    make_snapshots,
)

NAME = re.compile(r"clip_f(\d{3})_00h00m0\ds\.jpg")


def test_frames_are_named_after_the_video_and_moment(clip, tmp_path):
    paths = make_snapshots(clip, tmp_path / "frames", SnapshotOptions(count=5))
    numbers = [int(NAME.fullmatch(p.name).group(1)) for p in paths]
    assert numbers == [1, 2, 3, 4, 5]
    with Image.open(paths[0]) as image:
        assert image.size == (160, 90)
        assert image.format == "JPEG"


def test_precise_snapshots_follow_the_video(clip, tmp_path, gray_index):
    # Timestamps 0.5, 1.0, ..., 2.5 s at 10 fps.
    paths = make_snapshots(clip, tmp_path, SnapshotOptions(count=5, sampling="precise"))
    indices = []
    for path in paths:
        with Image.open(path) as image:
            indices.append(gray_index(image))
    assert indices == [5, 10, 15, 20, 25]


def test_padding_grows_with_count(clip, tmp_path):
    paths = make_snapshots(clip, tmp_path, SnapshotOptions(count=1000, jpeg_quality=10))
    assert paths[0].name.startswith("clip_f0001_")
    named = make_snapshots(clip, tmp_path / "n", SnapshotOptions(count=1), name="clip.mp4")
    assert named[0].name.startswith("clip.mp4_f001_")


def test_frame_names():
    assert frame_name("clip", 1, 754.9) == "clip_f001_00h12m34s.jpg"
    assert frame_name("clip", 12, 3 * 3600 + 5, pad=4) == "clip_f0012_03h00m05s.jpg"


def test_missing_snapshots_are_logged(clip, tmp_path, monkeypatch, caplog):
    from videothumb import snapshots

    real = snapshots.iter_frames

    def lose_second_frame(*args, **kwargs):
        for index, image in real(*args, **kwargs):
            yield index, None if index == 1 else image

    monkeypatch.setattr(snapshots, "iter_frames", lose_second_frame)

    paths = make_snapshots(clip, tmp_path, SnapshotOptions(count=4))

    assert [p.name.split("_")[1] for p in paths] == ["f001", "f003", "f004"]
    assert "Saved 3/4 frames" in caplog.text


def test_video_without_video_stream(audio_only, tmp_path):
    with pytest.raises(ValueError, match="No video stream"):
        make_snapshots(audio_only, tmp_path)


def test_default_snapshot_dir(tmp_path):
    assert default_snapshot_dir(tmp_path / "movie.mp4") == tmp_path / "movie_frames"


@pytest.mark.parametrize("kwargs", [{"count": 0}, {"jpeg_quality": 101}])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        SnapshotOptions(**kwargs)
