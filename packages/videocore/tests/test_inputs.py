import io
import sys
from pathlib import Path

import pytest

from videocore import UsageError, collect_inputs, expand_path, read_input_list

BOM = chr(0xFEFF)  # Notepad starts UTF-8 files with it
BAD = chr(0xFFFD)  # what a decoder writes for bytes it cannot read


@pytest.fixture
def files(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("a.mp4", "clip [1080p].mp4", "sub/b.mkv", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def names(paths):
    return [Path(p).name for p in paths]


def test_existing_paths_are_used_as_they_are(files):
    # Brackets would be a character class as a pattern; the file exists, so it is used.
    assert expand_path(files / "clip [1080p].mp4") == [files / "clip [1080p].mp4"]


def test_patterns_expand_only_when_the_path_does_not_exist(files):
    assert names(expand_path(str(files / "*.mp4"))) == ["a.mp4", "clip [1080p].mp4"]
    assert names(expand_path(str(files / "**" / "*.mkv"))) == ["b.mkv"]
    with pytest.raises(UsageError, match="No files match"):
        expand_path(str(files / "*.avi"))
    with pytest.raises(UsageError, match="Not found"):
        expand_path(files / "missing.mp4")
    with pytest.raises(UsageError, match="use --input-list"):
        expand_path(files / "list.txt")


def test_input_lists(files):
    listing = files / "list.txt"
    listing.write_text(
        BOM + f"# videos to archive\n\n  \"{files / 'a.mp4'}\"  \nsub/b.mkv\n'clip [1080p].mp4'\n",
        encoding="utf-8",
    )
    paths, warnings = read_input_list(listing)
    assert paths == [files / "a.mp4", files / "sub/b.mkv", files / "clip [1080p].mp4"]
    assert warnings == []


@pytest.mark.skipif(sys.platform == "win32", reason="backslashes are separators on Windows")
def test_lists_written_on_windows_work_elsewhere(files):
    (files / "odd\\name.mp4").write_bytes(b"x")  # a real name with a backslash wins
    listing = files / "list.txt"
    listing.write_text("sub\\b.mkv\nodd\\name.mp4\nsub\\missing.mp4\n", encoding="utf-8")
    paths, _ = read_input_list(listing)
    assert paths == [files / "sub/b.mkv", files / "odd\\name.mp4", files / "sub/missing.mp4"]


def test_standard_input_is_relative_to_the_current_folder(files, monkeypatch):
    monkeypatch.chdir(files)
    paths, warnings = read_input_list("-", stdin=io.StringIO(f"a.mp4\nbad{BAD}name.mp4\n"))
    assert paths == [files / "a.mp4", files / f"bad{BAD}name.mp4"]
    assert "replacement characters" in warnings[0] and "--input-list FILE" in warnings[0]


def test_unreadable_lists_are_usage_errors(files):
    with pytest.raises(UsageError, match="Input list not found"):
        read_input_list(files / "missing.txt")
    (files / "latin1.txt").write_bytes("vídeo.mp4\n".encode("latin-1"))
    with pytest.raises(UsageError, match="not UTF-8"):
        read_input_list(files / "latin1.txt")


def test_collect_joins_every_source_once(files):
    listing = files / "list.txt"
    listing.write_text("a.mp4\nmissing.mp4\nsub\n", encoding="utf-8")
    inputs = collect_inputs([files / "a.mp4", files], [listing], recursive=False)
    assert names(inputs.videos) == ["a.mp4", "clip [1080p].mp4", "b.mkv"]
    assert inputs.roots == [None, files, files / "sub"]
    assert [(i.path.name, i.reason) for i in inputs.invalid] == [("missing.mp4", "not found")]
    assert not inputs.single_file and inputs.source is None
    assert len(inputs) == 4


def test_folders_follow_recursive_and_skip_dir(files):
    assert names(collect_inputs([files]).videos) == ["a.mp4", "clip [1080p].mp4"]
    deep = collect_inputs([files], recursive=True)
    assert names(deep.videos) == ["a.mp4", "clip [1080p].mp4", "b.mkv"]
    assert deep.source == files
    skipped = collect_inputs([files], recursive=True, skip_dir=lambda p: p.name == "sub")
    assert names(skipped.videos) == ["a.mp4", "clip [1080p].mp4"]


def test_one_file_given_by_itself(files):
    inputs = collect_inputs([files / "notes.txt"])  # used whatever its extension
    assert inputs.single_file and names(inputs.videos) == ["notes.txt"]
    assert not collect_inputs([files / "a.mp4", files / "a.mp4"]).single_file


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path limit")
def test_a_missing_path_too_long_for_windows_says_why(tmp_path):
    with pytest.raises(UsageError, match="longer than Windows allows"):
        expand_path(tmp_path / ("x" * 250) / "clip.mp4")
