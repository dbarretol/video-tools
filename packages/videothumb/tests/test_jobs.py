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


@pytest.mark.parametrize(
    ("mode", "outputs"),
    [
        (Mode.SHEET, ["clip.mkv_sheet.jpg", "clip.mp4_sheet.jpg"]),
        (Mode.SNAPSHOTS, ["clip.mkv_snapshots", "clip.mp4_snapshots"]),
    ],
)
def test_videos_sharing_a_stem_do_not_overwrite_each_other(clip, tmp_path, mode, outputs):
    for name in ("clip.mp4", "clip.mkv"):
        (tmp_path / name).write_bytes(clip.read_bytes())
    options = SHEET if mode is Mode.SHEET else SNAPSHOTS

    results = process_folder(find_videos(tmp_path), mode, options, workers=1)

    assert all(r.ok for r in results)
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix not in (".mp4", ".mkv")) == outputs


def test_recursive_batch_recreates_subfolders_in_the_output(clip, tmp_path):
    source, out = tmp_path / "videos", tmp_path / "out"
    for sub in ("x", "y"):
        (source / sub).mkdir(parents=True)
        (source / sub / "clip.mp4").write_bytes(clip.read_bytes())

    videos = find_videos(source, recursive=True)
    results = process_folder(videos, Mode.SHEET, SHEET, root=out, source=source, workers=1)

    assert [r.value for r in results] == [
        out / "x" / "clip_sheet.jpg",
        out / "y" / "clip_sheet.jpg",
    ]
    assert all(r.value.is_file() for r in results)


@pytest.mark.parametrize("mode", [Mode.SHEET, Mode.SNAPSHOTS])
def test_cancelled_batch_removes_part_files(folder, mode):
    if mode is Mode.SHEET:
        leftover = folder / "a_sheet.jpg.part"
    else:
        (folder / "a").mkdir()
        leftover = folder / "a" / "snapshot_002.jpg.part"
    leftover.write_bytes(b"killed worker")
    options = SHEET if mode is Mode.SHEET else SNAPSHOTS

    process_folder(find_videos(folder), mode, options, workers=1, should_cancel=lambda: True)

    assert not leftover.exists()


def test_outputs_have_no_part_files_left(clip, tmp_path):
    video = tmp_path / "movie.mp4"
    video.write_bytes(clip.read_bytes())
    process(video, Mode.SHEET, SHEET)
    process(video, Mode.SNAPSHOTS, SNAPSHOTS)
    assert not list(tmp_path.rglob("*.part"))


def test_process_folder_can_be_cancelled(folder):
    results = process_folder(
        find_videos(folder), Mode.SNAPSHOTS, SNAPSHOTS, workers=1, should_cancel=lambda: True
    )
    assert results == []
