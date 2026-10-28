from pathlib import Path

import pytest

from videocore import find_videos
from videothumb.common import Mode
from videothumb.jobs import batch_output, default_output, process, process_folder
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

SHEET = SheetOptions(columns=2, rows=2, thumbnail_width=80)
SNAPSHOTS = SnapshotOptions(count=3)


def test_default_and_batch_outputs():
    video = Path("videos/movie.mp4")
    assert default_output(video, Mode.SHEET) == Path("videos/movie_sheet.jpg")
    assert default_output(video, Mode.SNAPSHOTS) == Path("videos/movie")
    assert batch_output(video, Mode.SHEET, None) == Path("videos/movie_sheet.jpg")
    assert batch_output(video, Mode.SHEET, Path("out")) == Path("out/movie_sheet.jpg")
    assert batch_output(video, Mode.SNAPSHOTS, Path("out")) == Path("out/movie")


def test_process_uses_default_output_next_to_the_video(clip, tmp_path):
    video = tmp_path / "movie.mp4"
    video.write_bytes(clip.read_bytes())
    assert process(video, Mode.SHEET, SHEET) == tmp_path / "movie_sheet.jpg"
    assert process(video, Mode.SNAPSHOTS, SNAPSHOTS) == tmp_path / "movie"
    assert len(list((tmp_path / "movie").glob("*.jpg"))) == 3


@pytest.mark.parametrize("workers", [1, 2])
def test_process_folder_isolates_failures(folder, tmp_path, workers):
    out = tmp_path / "out"
    results = process_folder(find_videos(folder), Mode.SHEET, SHEET, root=out, workers=workers)

    by_name = {r.item.name: r for r in results}
    assert sorted(by_name) == ["a.mp4", "b.mkv", "broken.mp4"]
    assert by_name["a.mp4"].ok and by_name["a.mp4"].value == out / "a_sheet.jpg"
    assert by_name["b.mkv"].ok and (out / "b_sheet.jpg").is_file()
    assert not by_name["broken.mp4"].ok
    assert "VideoOpenError" in by_name["broken.mp4"].error


def test_process_folder_can_be_cancelled(folder):
    results = process_folder(
        find_videos(folder), Mode.SNAPSHOTS, SNAPSHOTS, workers=1, should_cancel=lambda: True
    )
    assert results == []
