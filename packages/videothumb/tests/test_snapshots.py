import pytest
from PIL import Image

from videothumb.snapshots import SnapshotOptions, default_snapshot_dir, make_snapshots


def test_snapshots_are_numbered_full_resolution_jpegs(clip, tmp_path):
    paths = make_snapshots(clip, tmp_path / "snaps", SnapshotOptions(count=5))
    assert [p.name for p in paths] == [f"snapshot_{i:03d}.jpg" for i in range(1, 6)]
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
    assert paths[0].name == "snapshot_0001.jpg"


def test_video_without_video_stream(audio_only, tmp_path):
    with pytest.raises(ValueError, match="No video stream"):
        make_snapshots(audio_only, tmp_path)


def test_default_snapshot_dir(tmp_path):
    assert default_snapshot_dir(tmp_path / "movie.mp4") == tmp_path / "movie"


@pytest.mark.parametrize("kwargs", [{"count": 0}, {"jpeg_quality": 101}])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        SnapshotOptions(**kwargs)
