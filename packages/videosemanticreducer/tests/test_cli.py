import dataclasses
import logging
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


def test_recursive_process_skips_its_own_outputs(clip, tmp_path):
    folder = tmp_path / "videos"
    (folder / "sub").mkdir(parents=True)
    shutil.copy(clip, folder / "a.mp4")
    shutil.copy(clip, folder / "sub" / "b.mp4")
    args = ["process", str(folder), "-r", "-w", "1", "--no-fingerprint", *FAST]

    assert main(args) == 0
    assert (folder / "a_summary" / "a_summary.mp4").is_file()
    # A second pass finds the same two videos, not the summary videos it wrote.
    assert main(args) == 0
    assert not (folder / "a_summary" / "a_summary_summary").exists()
    assert not (folder / "sub" / "b_summary" / "b_summary_summary").exists()


def test_process_audio_only_fails(audio_only, tmp_path):
    assert main(["process", str(audio_only), "-o", str(tmp_path / "out")]) == 1


def test_process_missing_input_is_a_usage_error(tmp_path):
    assert main(["process", str(tmp_path / "missing.mp4")]) == 2


@pytest.mark.parametrize("position", ["before", "after"])
def test_verbose_works_before_or_after_the_subcommand(tmp_path, position, monkeypatch):
    levels = []
    monkeypatch.setattr(
        "videosemanticreducer.cli.configure_logging",
        lambda name, level, console: levels.append(level) or tmp_path / "x.log",
    )
    missing = str(tmp_path / "missing.mp4")
    argv = ["-v", "process", missing] if position == "before" else ["process", missing, "-v"]
    main(argv)
    assert levels == [logging.DEBUG]


def test_without_verbose_logs_at_info(tmp_path, monkeypatch):
    levels = []
    monkeypatch.setattr(
        "videosemanticreducer.cli.configure_logging",
        lambda name, level, console: levels.append(level) or tmp_path / "x.log",
    )
    main(["process", str(tmp_path / "missing.mp4")])
    assert levels == [logging.INFO]


@pytest.mark.parametrize(
    ("option", "value"),
    [("-n", "0"), ("-d", "-1"), ("--thumb-cols", "x"), ("--thumb-rows", "0"), ("-w", "0")],
)
def test_invalid_option_values_are_rejected(clip, capsys, option, value):
    with pytest.raises(SystemExit) as exc:
        main(["process", str(clip), option, value])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "must be greater than 0" in err or "expected" in err


def test_complete_archives_are_skipped_unless_overwrite(clip, tmp_path, caplog):
    out = tmp_path / "out"
    args = ["process", str(clip), "-o", str(out), "--no-fingerprint", *FAST]
    assert main(args) == 0
    first = (out / "clip_thumb.jpg").stat().st_mtime_ns

    caplog.clear()
    assert main(args) == 0
    assert "already archived" in caplog.text
    assert (out / "clip_thumb.jpg").stat().st_mtime_ns == first

    assert main([*args, "--overwrite"]) == 0
    assert (out / "clip_thumb.jpg").stat().st_mtime_ns != first


def test_newly_requested_artifacts_are_not_skipped(clip, tmp_path):
    out = tmp_path / "out"
    base = ["process", str(clip), "-o", str(out), *FAST]
    assert main([*base, "--no-fingerprint"]) == 0
    assert main(base) == 0  # the fingerprint was not made the first time
    assert (out / "clip.fingerprint.json").is_file()


def test_archives_made_with_other_settings_are_reported_not_redone(clip, tmp_path, caplog):
    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "a.mp4")
    base = ["process", str(folder), "-w", "1", "--no-fingerprint", "--no-video"]
    assert main([*base, "--thumb-cols", "2", "--thumb-rows", "2"]) == 0

    caplog.clear()
    assert main([*base, "--thumb-cols", "3", "--thumb-rows", "2"]) == 0
    assert "1 archived with other settings (not redone)" in caplog.text
    assert "--overwrite" in caplog.text


def test_incomplete_archive_is_redone(clip, tmp_path):
    out = tmp_path / "out"
    args = ["process", str(clip), "-o", str(out), "--no-fingerprint", *FAST]
    assert main(args) == 0
    (out / "clip_thumb.jpg").unlink()  # the manifest no longer matches the folder
    assert main(args) == 0
    assert (out / "clip_thumb.jpg").is_file()


def test_ctrl_c_exits_with_130(clip, tmp_path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("videosemanticreducer.pipeline.process_video", interrupted)
    assert main(["process", str(clip), "-o", str(tmp_path / "out")]) == 130


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


def test_verify_and_compare_exit_1_when_the_video_differs(clip, tmp_path, monkeypatch):
    from videosemanticreducer import cli

    saved = tmp_path / "clip.fingerprint.json"
    assert main(["fingerprint", str(clip), "-o", str(saved)]) == 0
    real = cli.compare_fingerprints

    def as_if_different(fp1, fp2):
        return dataclasses.replace(real(fp1, fp2), match_level="LOW")

    monkeypatch.setattr(cli, "compare_fingerprints", as_if_different)
    assert main(["verify", str(clip), str(saved)]) == 1
    assert main(["compare", str(saved), str(saved)]) == 1


def test_verify_errors_exit_2(clip, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["verify", str(clip), str(bad)]) == 2
    assert main(["verify", str(tmp_path / "missing.mp4"), str(bad)]) == 2


def test_compare_invalid_fingerprint(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["compare", str(bad), str(bad)]) == 2


def test_dry_run_writes_nothing(clip, tmp_path, capsys):
    folder = tmp_path / "videos"
    folder.mkdir()
    for name in ("a.mp4", "b.mp4"):
        shutil.copy(clip, folder / name)
    args = ["process", str(folder), "-w", "1", "--no-video", *FAST]
    assert main([*args, "--no-fingerprint"]) == 0  # a is archived without the fingerprint...
    shutil.rmtree(folder / "b_summary")
    capsys.readouterr()

    assert main([*args, "--no-fingerprint", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "2 videos: 1 to process, 1 already done" in out
    assert "[process] b.mp4 -> b_summary" in out
    assert not (folder / "b_summary").exists()

    # ...so asking for it now makes a pending again.
    assert main([*args, "--dry-run"]) == 0
    assert "2 videos: 2 to process" in capsys.readouterr().out


def test_dry_run_of_one_video(clip, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["process", str(clip), "-o", str(out), "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "1 video: 1 to process" in text and "estimated time" in text
    assert not out.exists()


def test_batch_logs_the_plan_first(clip, tmp_path, caplog):
    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "a.mp4")
    caplog.set_level(logging.INFO)
    assert main(["process", str(folder), "-w", "1", "--no-video", *FAST]) == 0
    messages = [r.getMessage() for r in caplog.records]
    plan_line = messages.index("1 video: 1 to process")
    assert plan_line < next(i for i, m in enumerate(messages) if m.startswith("[1/1]"))


def test_failures_come_with_a_hint_and_the_log_path(audio_only, clip, tmp_path, caplog):
    assert main(["process", str(audio_only), "-o", str(tmp_path / "out")]) == 1
    assert "Hint: it has no video to take frames from" in caplog.text
    assert "Details: " in caplog.text and "videosemanticreducer" in caplog.text

    caplog.clear()
    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "good.mp4")
    (folder / "broken.mp4").write_bytes(b"not a video")
    assert main(["process", str(folder), "-w", "1", "--no-video", *FAST]) == 1
    assert "Hint: the file may be damaged" in caplog.text


def test_interrupted_batch_says_how_to_resume(clip, tmp_path, monkeypatch, caplog):
    from videocore import default_runs_dir
    from videosemanticreducer import pipeline

    folder = tmp_path / "videos"
    folder.mkdir()
    for name in ("a.mp4", "b.mp4"):
        shutil.copy(clip, folder / name)
    real = pipeline.process_video

    def stop_at_b(video, *args, **kwargs):
        if video.name == "b.mp4":
            raise KeyboardInterrupt
        return real(video, *args, **kwargs)

    monkeypatch.setattr(pipeline, "process_video", stop_at_b)
    assert main(["process", str(folder), "-w", "1", "--no-video", *FAST]) == 130
    assert "1 archived" in caplog.text and "1 not finished" in caplog.text
    assert "run the same command again to resume" in caplog.text
    (pending,) = default_runs_dir("videosemanticreducer").glob("*_archive_pending.txt")
    assert str((folder / "b.mp4").resolve()) in pending.read_text(encoding="utf-8-sig")
