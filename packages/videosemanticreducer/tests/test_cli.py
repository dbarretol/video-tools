import shutil

import pytest

from videosemanticreducer.cli import main

FAST = ["-n", "6", "--thumb-cols", "3", "--thumb-rows", "2"]


def test_without_arguments_prints_help(capsys):
    assert main([]) == 0
    assert "process" in capsys.readouterr().out


def test_process_single_video(clip, tmp_path):
    out = tmp_path / "out"
    assert main(["process", str(clip), "-o", str(out), *FAST]) == 0
    assert (out / "clip_summary.mp4").is_file()
    assert (out / "clip.fingerprint.json").is_file()


@pytest.mark.parametrize("workers", ["1", "2"])
def test_process_folder(clip, tmp_path, workers):
    folder = tmp_path / "videos"
    folder.mkdir()
    for name in ("a.mp4", "b.mp4"):
        shutil.copy(clip, folder / name)
    (folder / "notes.txt").write_text("not a video")
    out = tmp_path / "out"

    args = ["process", str(folder), "-o", str(out), "-w", workers, "--no-video", *FAST]
    assert main(args) == 0
    assert (out / "a_summary" / "a_thumb.jpg").is_file()
    assert (out / "b_summary" / "b.fingerprint.json").is_file()


def test_process_folder_reports_failures(clip, tmp_path):
    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "good.mp4")
    (folder / "broken.mp4").write_bytes(b"not a video")
    args = ["process", str(folder), "-o", str(tmp_path / "out"), "-w", "1", "--no-video", *FAST]
    assert main(args) == 1
    assert (tmp_path / "out" / "good_summary" / "good_info.txt").is_file()


def test_process_missing_input(tmp_path):
    assert main(["process", str(tmp_path / "missing.mp4")]) == 1


def test_fingerprint_compare_and_verify(clip, tmp_path, capsys):
    original, copy = tmp_path / "original.mp4", tmp_path / "copy.mp4"
    shutil.copy(clip, original)
    shutil.copy(clip, copy)
    assert main(["fingerprint", str(original)]) == 0
    saved = tmp_path / "original.mp4.fingerprint.json"
    assert saved.is_file()
    custom = tmp_path / "copy.json"
    assert main(["fingerprint", str(copy), "-o", str(custom)]) == 0
    capsys.readouterr()

    assert main(["compare", str(saved), str(custom)]) == 0
    assert "Match level:       EXACT" in capsys.readouterr().out

    assert main(["verify", str(copy), str(saved)]) == 0
    assert "matches the original" in capsys.readouterr().out


def test_compare_invalid_fingerprint(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["compare", str(bad), str(bad)]) == 1
