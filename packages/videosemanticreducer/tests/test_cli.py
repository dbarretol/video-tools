import dataclasses
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


def spy_logging(monkeypatch, tmp_path):
    calls = []

    def setup(app, console, *, verbose):
        calls.append((verbose, console.quiet))
        return tmp_path / "x.log"

    monkeypatch.setattr("videosemanticreducer.cli.setup_logging", setup)
    return calls


@pytest.mark.parametrize("position", ["before", "after"])
def test_verbose_and_quiet_work_before_or_after_the_subcommand(tmp_path, position, monkeypatch):
    calls = spy_logging(monkeypatch, tmp_path)
    missing = str(tmp_path / "missing.mp4")
    argv = ["-v", "process", missing] if position == "before" else ["process", missing, "-v"]
    main(argv)
    flags = ["-q", "status", missing] if position == "before" else ["status", missing, "-q"]
    main(flags)
    assert calls == [(True, False), (False, True)]


def test_logs_go_to_the_console_only_with_verbose(tmp_path, monkeypatch, capsys):
    calls = spy_logging(monkeypatch, tmp_path)
    main(["process", str(tmp_path / "missing.mp4")])
    assert calls == [(False, False)]
    assert capsys.readouterr().err.startswith("Error: Input not found")


def test_debug_messages_print_with_verbose(clip, tmp_path, capsys):
    assert main(["process", str(clip), "-o", str(tmp_path / "out"), "-v", *FAST]) == 0
    assert "summary video" in capsys.readouterr().err  # the stages of the video


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


def test_complete_archives_are_skipped_unless_overwrite(clip, tmp_path, capsys):
    out = tmp_path / "out"
    args = ["process", str(clip), "-o", str(out), "--no-fingerprint", *FAST]
    assert main(args) == 0
    first = (out / "clip_thumb.jpg").stat().st_mtime_ns
    assert f"Archived {clip} in {out}" in capsys.readouterr().out

    assert main(args) == 0
    assert "already archived" in capsys.readouterr().out
    assert (out / "clip_thumb.jpg").stat().st_mtime_ns == first

    assert main([*args, "--overwrite"]) == 0
    assert (out / "clip_thumb.jpg").stat().st_mtime_ns != first


def test_newly_requested_artifacts_are_not_skipped(clip, tmp_path):
    out = tmp_path / "out"
    base = ["process", str(clip), "-o", str(out), *FAST]
    assert main([*base, "--no-fingerprint"]) == 0
    assert main(base) == 0  # the fingerprint was not made the first time
    assert (out / "clip.fingerprint.json").is_file()


def test_archives_made_with_other_settings_are_reported_not_redone(clip, tmp_path, capsys):
    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "a.mp4")
    base = ["process", str(folder), "-w", "1", "--no-fingerprint", "--no-video"]
    assert main([*base, "--thumb-cols", "2", "--thumb-rows", "2"]) == 0

    capsys.readouterr()
    assert main([*base, "--thumb-cols", "3", "--thumb-rows", "2"]) == 0
    out = capsys.readouterr().out
    assert "1 archived with other settings (not redone)" in out
    assert "--overwrite" in out


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


def test_batch_prints_the_plan_first(clip, tmp_path, capsys):
    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "a.mp4")
    assert main(["process", str(folder), "-w", "1", "--no-video", *FAST]) == 0
    out = capsys.readouterr().out
    assert out.index("1 video: 1 to process") < out.index("[1/1] a.mp4: OK")
    assert "Done: 1 archived, 0 failed." in out


def test_failures_come_with_a_hint_and_the_log_path(audio_only, clip, tmp_path, capsys):
    assert main(["process", str(audio_only), "-o", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert "Hint: it has no video to take frames from" in err
    assert "Details: " in err and "videosemanticreducer" in err

    folder = tmp_path / "videos"
    folder.mkdir()
    shutil.copy(clip, folder / "good.mp4")
    (folder / "broken.mp4").write_bytes(b"not a video")
    assert main(["process", str(folder), "-w", "1", "--no-video", *FAST]) == 1
    assert "    Hint: the file may be damaged" in capsys.readouterr().err


def test_interrupted_batch_says_how_to_resume(clip, tmp_path, monkeypatch, capsys):
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
    captured = capsys.readouterr()
    assert "Done: 1 archived, 0 failed, 1 not finished." in captured.out
    assert "to resume, run the same command again" in captured.out
    assert "Interrupted" in captured.err
    (pending,) = default_runs_dir("videosemanticreducer").glob("*_archive_pending.txt")
    assert str((folder / "b.mp4").resolve()) in pending.read_text(encoding="utf-8-sig")


def test_process_and_status_json(clip, tmp_path, capsys):
    import json

    video = tmp_path / "vídeo.mp4"  # non-ASCII names are escaped, whatever the console encoding
    shutil.copy(clip, video)
    assert main(["process", str(video), "--json", *FAST]) == 0
    out = capsys.readouterr().out
    assert out.isascii()
    document = json.loads(out)
    assert document["command"] == "process" and document["exit_code"] == 0
    (item,) = document["report"]["items"]
    assert item["status"] == "ok" and item["video"] == str(video)

    assert main(["status", str(tmp_path), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    (archive,) = document["archives"]
    assert archive["status"] == "ready" and archive["video"] == str(video)

    (tmp_path / "other.mp4").write_bytes(b"x")
    assert main(["status", str(tmp_path), "--json"]) == 1
    statuses = [a["status"] for a in json.loads(capsys.readouterr().out)["archives"]]
    assert statuses == ["not_archived", "ready"]
