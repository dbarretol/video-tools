import os
from pathlib import Path

import pytest

from videocore import (
    OutputCollisionError,
    OutputSlot,
    atomic_write,
    output_folder,
    part_path,
    plan_outputs,
    remove_partials,
)


def test_names_are_stems_when_nothing_collides(tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mkv"]
    assert plan_outputs(videos) == [OutputSlot(tmp_path, "a"), OutputSlot(tmp_path, "b")]


def test_shared_stem_in_the_same_folder_uses_the_file_name(tmp_path, caplog):
    videos = [tmp_path / "clip.mp4", tmp_path / "clip.mkv", tmp_path / "other.mp4"]

    slots = plan_outputs(videos)

    assert [slot.name for slot in slots] == ["clip.mp4", "clip.mkv", "other"]
    assert [slot.renamed for slot in slots] == [True, True, False]
    assert "share a name" in caplog.text


def test_same_stem_in_different_folders_does_not_collide(tmp_path):
    videos = [tmp_path / "x" / "clip.mp4", tmp_path / "y" / "clip.mp4"]
    assert [slot.name for slot in plan_outputs(videos)] == ["clip", "clip"]


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive file names")
def test_stems_differing_only_in_case_collide_on_windows(tmp_path):
    videos = [tmp_path / "Clip.mp4", tmp_path / "clip.mkv"]
    assert [slot.name for slot in plan_outputs(videos)] == ["Clip.mp4", "clip.mkv"]


def test_one_output_folder_gathers_videos_from_several_folders(tmp_path):
    out = tmp_path / "out"
    videos = [tmp_path / "x" / "clip.mp4", tmp_path / "y" / "clip.mkv"]

    slots = plan_outputs(videos, output=out)

    assert slots == [OutputSlot(out, "clip.mp4", True), OutputSlot(out, "clip.mkv", True)]


def test_identical_file_names_in_one_output_folder_are_an_error(tmp_path):
    videos = [tmp_path / "x" / "clip.mp4", tmp_path / "y" / "clip.mp4"]
    with pytest.raises(OutputCollisionError, match="overwrite each other"):
        plan_outputs(videos, output=tmp_path / "out")


def test_rename_that_still_collides_is_an_error(tmp_path):
    # clip.mp4 and clip.mkv become "clip.mp4" and "clip.mkv"; clip.mp4.mkv's stem is "clip.mp4".
    videos = [tmp_path / "clip.mp4", tmp_path / "clip.mkv", tmp_path / "clip.mp4.mkv"]
    with pytest.raises(OutputCollisionError):
        plan_outputs(videos)


def test_source_folders_are_recreated_inside_the_output(tmp_path):
    source, out = tmp_path / "videos", tmp_path / "out"
    videos = [source / "x" / "clip.mp4", source / "y" / "clip.mp4", source / "top.mp4"]

    slots = plan_outputs(videos, output=out, source=source)

    assert slots == [
        OutputSlot(out / "x", "clip"),
        OutputSlot(out / "y", "clip"),
        OutputSlot(out, "top"),
    ]


def test_output_folder(tmp_path):
    video = tmp_path / "videos" / "sub" / "clip.mp4"
    out = tmp_path / "out"
    assert output_folder(video, None, None) == video.parent
    assert output_folder(video, out, None) == out
    assert output_folder(video, out, tmp_path / "videos") == out / "sub"
    # A video outside the scanned folder goes straight into the output.
    assert output_folder(video, out, tmp_path / "elsewhere") == out


def test_empty_batch():
    assert plan_outputs([], output=Path("out")) == []


def test_atomic_write_renames_when_done(tmp_path):
    target = tmp_path / "sub" / "sheet.jpg"
    with atomic_write(target) as part:
        assert part == tmp_path / "sub" / "sheet.jpg.part"
        part.write_bytes(b"new")
        assert not target.exists()
    assert target.read_bytes() == b"new"
    assert not part.exists()


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_atomic_write_keeps_the_old_file_on_error(tmp_path, error):
    target = tmp_path / "sheet.jpg"
    target.write_bytes(b"old")
    with pytest.raises(error), atomic_write(target) as part:
        part.write_bytes(b"half")
        raise error
    assert target.read_bytes() == b"old"
    assert not part_path(target).exists()


def test_remove_partials(tmp_path):
    for name in ("a.jpg.part", "a.jpg", "b.mp4.part", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "dir.part").mkdir()

    assert remove_partials(tmp_path, prefix="a") == [tmp_path / "a.jpg.part"]
    assert remove_partials(tmp_path) == [tmp_path / "b.mp4.part"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "dir.part", "notes.txt"]
    assert remove_partials(tmp_path / "missing") == []
