from pathlib import Path

import pytest

from videothumb import cli


def test_without_arguments_shows_the_help_and_gui_opens_the_app(monkeypatch, capsys):
    launched = []
    monkeypatch.setattr("videothumb.gui.app.launch", lambda: launched.append(True))
    assert cli.main([]) == 0
    assert "sheet" in capsys.readouterr().out and launched == []
    assert cli.main(["gui"]) == 0
    assert launched == [True]


def test_sheet_for_one_video(clip, tmp_path, capsys):
    out = tmp_path / "sheet.jpg"
    args = [
        "sheet",
        str(clip),
        "--columns",
        "2",
        "--rows",
        "2",
        "-o",
        str(out),
    ]
    assert cli.main(args) == 0
    assert out.is_file()
    assert "Contact sheet saved" in capsys.readouterr().out


def test_frames_for_one_video(clip, tmp_path):
    out = tmp_path / "frames"
    args = [
        "frames",
        str(clip),
        "--count",
        "4",
        "--precise",
        "-o",
        str(out),
    ]
    assert cli.main(args) == 0
    assert len(list(out.glob("clip_f00[1-4]_00h00m0[0-9]s.jpg"))) == 4


def test_folder_reports_failures(folder, tmp_path, capsys):
    out = tmp_path / "out"
    args = [
        "frames",
        str(folder),
        "--count",
        "2",
        "-w",
        "1",
        "-o",
        str(out),
    ]
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    assert "Done: 2 succeeded, 1 failed." in captured.out
    assert "broken.mp4" in captured.err
    assert (out / "a_frames").is_dir() and (out / "b_frames").is_dir()


def test_empty_folder(tmp_path, capsys):
    assert cli.main(["sheet", str(tmp_path)]) == 0
    assert "No videos found" in capsys.readouterr().out


def test_missing_video_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["sheet", str(tmp_path / "missing.mp4")]) == 2
    assert "Not found" in capsys.readouterr().err


def test_missing_folder_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["sheet", str(tmp_path / "missing")]) == 2
    assert "Not found" in capsys.readouterr().err


def test_broken_video_fails(tmp_path, capsys):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert cli.main(["sheet", str(broken)]) == 1


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--columns", "0"),
        ("--rows", "-1"),
        ("--count", "zero"),
        ("--workers", "0"),
        ("--quality", "101"),
        ("--thumbnail-width", "8"),
    ],
)
def test_invalid_option_values_are_rejected(clip, capsys, option, value):
    with pytest.raises(SystemExit) as exc:
        cli.main(["sheet", str(clip), option, value])
    assert exc.value.code == 2
    assert option in capsys.readouterr().err


def test_skip_existing_leaves_outputs_alone(folder, capsys):
    (folder / "a_sheet.jpg").write_bytes(b"from an earlier run")
    args = ["sheet", str(folder), "-w", "1", "--skip-existing"]

    assert cli.main(args) == 1  # broken.mp4 still fails
    assert "1 succeeded, 1 skipped (output already exists), 1 failed." in capsys.readouterr().out
    assert (folder / "a_sheet.jpg").read_bytes() == b"from an earlier run"
    assert (folder / "b_sheet.jpg").is_file()


def test_outputs_are_overwritten_by_default(clip, tmp_path, capsys):
    out = tmp_path / "sheet.jpg"
    out.write_bytes(b"from an earlier run")
    assert cli.main(["sheet", str(clip), "-o", str(out)]) == 0
    assert out.read_bytes() != b"from an earlier run"

    args = ["sheet", str(clip), "-o", str(out), "--skip-existing"]
    before = out.read_bytes()
    assert cli.main(args) == 0
    assert "Skipped" in capsys.readouterr().out
    assert out.read_bytes() == before


def test_overwrite_and_skip_existing_are_exclusive(clip):
    with pytest.raises(SystemExit) as exc:
        cli.main(["sheet", str(clip), "--overwrite", "--skip-existing"])
    assert exc.value.code == 2


def test_ctrl_c_exits_with_130(clip, monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("videothumb.jobs.make_sheet", interrupted)
    assert cli.main(["sheet", str(clip)]) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_without_a_command_the_default_task_runs(clip, tmp_path, capsys):
    out = tmp_path / "s.jpg"
    assert cli.main([str(clip), "-o", str(out)]) == 0
    assert out.is_file() and "Contact sheet saved" in capsys.readouterr().out

    config = tmp_path / "c.yaml"
    config.write_text("console:\n  default_task: frames\n", encoding="utf-8")
    frames = tmp_path / "f"
    assert cli.main([str(clip), "-o", str(frames), "--count", "2", "--config", str(config)]) == 0
    assert len(list(frames.glob("clip_f*.jpg"))) == 2


def test_a_broken_config_is_reported_by_the_default_task(clip, tmp_path, capsys):
    config = tmp_path / "c.yaml"
    config.write_text("console:\n  default_task: cleanup\n", encoding="utf-8")
    assert cli.main([str(clip), "--config", str(config)]) == 2
    assert "default_task" in capsys.readouterr().err


def test_each_command_only_takes_its_own_options(clip, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["sheet", str(clip), "--count", "3"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --count" in capsys.readouterr().err


def test_inputs_and_lists(folder, tmp_path, capsys):
    listing = tmp_path / "list.txt"
    listing.write_text(f"{folder / 'b.mkv'}\ngone.mp4\n", encoding="utf-8")
    args = ["sheet", str(folder / "a.*"), "--input-list", str(listing), "--dry-run"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "3 videos: 2 to process, 1 not found" in out


def test_old_command_lines_still_work_with_a_warning(clip, folder, tmp_path, capsys):
    out = tmp_path / "s.jpg"
    assert cli.main(["--video", str(clip), "--mode", "sheet", "-o", str(out)]) == 0
    err = capsys.readouterr().err
    assert "--mode, --video and --folder are deprecated" in err
    assert f"use: videothumb sheet {clip} -o {out}" in err
    assert out.is_file()

    assert cli.main(["--folder", str(folder), "--mode=snapshots", "--count", "2", "-w", "1"]) == 1
    assert "use: videothumb frames" in capsys.readouterr().err
    assert (folder / "a_frames").is_dir()

    assert cli.main(["snapshots", str(clip), "-o", str(tmp_path / "f"), "--count", "1"]) == 0
    assert "`videothumb snapshots` is now `videothumb frames`" in capsys.readouterr().err


def test_options_share_one_declaration():
    from videothumb.sheet import SheetOptions
    from videothumb.snapshots import SnapshotOptions

    args = cli.build_parser().parse_args(["frames", "x", "--quality", "70"])
    assert cli.build_options(args) == SnapshotOptions(jpeg_quality=70)
    args = cli.build_parser().parse_args(["sheet", "x", "--quality", "70"])
    assert cli.build_options(args) == SheetOptions(jpeg_quality=70)


def test_dry_run_lists_what_would_happen(folder, capsys):
    (folder / "a_sheet.jpg").write_bytes(b"from an earlier run")
    args = ["sheet", str(folder), "--skip-existing", "--dry-run"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "3 videos: 2 to process, 1 already done" in out
    assert "[skip, already done] a.mp4 -> a_sheet.jpg" in out
    assert not (folder / "b_sheet.jpg").exists()


def test_batch_prints_the_plan_first(folder, capsys):
    assert cli.main(["sheet", str(folder), "-w", "1"]) == 1
    out = capsys.readouterr().out
    assert out.index("3 videos: 3 to process") < out.index("[1/3]")


def lose_frames(monkeypatch, keep):
    from videothumb import sheet

    real = sheet.sample_frames

    def partial(*args, **kwargs):
        frames = real(*args, **kwargs)
        return [frame if i < keep else None for i, frame in enumerate(frames)]

    monkeypatch.setattr(sheet, "sample_frames", partial)


def test_incomplete_sheet_is_degraded_and_exits_with_3(clip, tmp_path, monkeypatch, capsys):
    lose_frames(monkeypatch, keep=10)
    args = ["sheet", str(clip), "-o", str(tmp_path / "s.jpg")]
    assert cli.main(args) == 3
    err = capsys.readouterr().err
    assert "incomplete output: contact sheet: only 10/20 frames could be decoded" in err
    assert cli.main([*args, "--strict"]) == 1


def test_batch_reports_incomplete_outputs(folder, monkeypatch, capsys):
    lose_frames(monkeypatch, keep=1)
    (folder / "broken.mp4").unlink()
    assert cli.main(["sheet", str(folder), "-w", "1"]) == 3
    captured = capsys.readouterr()
    assert "a.mp4: DEGRADED" in captured.out
    assert "Done: 0 succeeded, 2 incomplete, 0 failed." in captured.out
    assert "a.mp4: incomplete: contact sheet: only 1/20" in captured.err


def test_sheet_of_the_wrong_size_fails(clip, tmp_path):
    from PIL import Image

    from videocore.verify import FrameCount, OutputError
    from videothumb.jobs import Produced, SheetTask
    from videothumb.sheet import SheetOptions

    path = tmp_path / "s.jpg"
    Image.new("RGB", (100, 100)).save(path)
    with pytest.raises(OutputError, match="100 pixels wide, expected 1600"):
        SheetTask().verify(path, Produced(path, FrameCount(20, 20)), SheetOptions())


@pytest.mark.parametrize(
    ("mode", "module", "function"),
    [("sheet", "sheet", "sample_frames"), ("frames", "snapshots", "iter_frames")],
)
def test_keyframe_sampling_falls_back_to_precise(
    clip, tmp_path, monkeypatch, mode, module, function
):
    import importlib

    module = importlib.import_module(f"videothumb.{module}")
    real, calls = getattr(module, function), []

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, function, spy)
    assert cli.main([mode, str(clip), "-o", str(tmp_path / "out")]) == 0
    assert calls[0]["fallback"] is True and calls[0]["mode"] == "keyframe"


def test_failures_come_with_a_hint_and_the_log_path(folder, tmp_path, capsys):
    broken = folder / "broken.mp4"
    assert cli.main(["sheet", str(broken)]) == 1
    err = capsys.readouterr().err
    assert "Hint: the file may be damaged" in err
    assert "Details: " in err and "videothumb" in err

    assert cli.main(["sheet", str(folder), "-w", "1"]) == 1
    err = capsys.readouterr().err
    assert "  - broken.mp4: VideoOpenError" in err
    assert "    Hint: the file may be damaged" in err


def test_interrupted_batch_lists_what_is_left(folder, monkeypatch, capsys):
    from pathlib import Path

    from videocore import default_runs_dir
    from videothumb import jobs

    real = jobs.make_sheet

    def stop_at_b(video, *args, **kwargs):
        if video.name.startswith("b"):
            raise KeyboardInterrupt
        return real(video, *args, **kwargs)

    monkeypatch.setattr(jobs, "make_sheet", stop_at_b)
    assert cli.main(["sheet", str(folder), "-w", "1"]) == 130
    captured = capsys.readouterr()
    assert "Interrupted" in captured.err
    assert "with --skip-existing" in captured.out
    (pending,) = default_runs_dir("videothumb").glob("*_sheet_pending.txt")
    lines = pending.read_text(encoding="utf-8-sig").splitlines()
    names = [Path(line).name for line in lines if not line.startswith("#")]
    assert names == ["b.mkv", "broken.mp4"]
    assert (folder / "a_sheet.jpg").is_file()


def test_failed_batch_lists_the_videos_to_retry(folder, capsys):
    from videocore import default_runs_dir

    assert cli.main(["sheet", str(folder), "-w", "1"]) == 1
    assert "Videos to retry are listed in" in capsys.readouterr().out
    (failed,) = default_runs_dir("videothumb").glob("*_sheet_failed.txt")
    assert str((folder / "broken.mp4").resolve()) in failed.read_text(encoding="utf-8-sig")


def test_quiet_leaves_only_what_needs_attention(folder, capsys):
    assert cli.main(["sheet", str(folder), "-w", "1", "-q"]) == 1
    captured = capsys.readouterr()
    assert "to process" not in captured.out and "Done:" not in captured.out
    assert "[1/3]" not in captured.out
    assert "Videos to retry are listed in" in captured.out
    assert "  - broken.mp4: VideoOpenError" in captured.err

    (folder / "broken.mp4").unlink()
    assert cli.main(["sheet", str(folder), "-w", "1", "-q"]) == 0
    assert capsys.readouterr() == ("", "")


def test_names_with_brackets_are_printed_as_they_are(clip, tmp_path, capsys):
    import shutil

    video = tmp_path / "clip [1080p] [bold]x.mp4"
    shutil.copy(clip, video)
    assert cli.main(["sheet", str(video)]) == 0
    assert "clip [1080p] [bold]x_sheet.jpg" in capsys.readouterr().out


def test_json_puts_one_document_on_stdout(folder, capsys):
    import json

    (folder / "a_sheet.jpg").write_bytes(b"from an earlier run")
    args = ["sheet", str(folder), "-w", "1", "--skip-existing", "--json"]
    assert cli.main(args) == 1
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["schema"] == 1 and document["tool"] == "videothumb"
    assert document["command"] == "sheet" and document["exit_code"] == 1
    assert document["plan"]["counts"] == {"pending": 2, "skipped": 1, "outdated": 0}
    items = document["report"]["items"]
    statuses = {Path(item["video"]).name: item["status"] for item in items}
    assert statuses == {"a.mp4": "skipped", "b.mkv": "ok", "broken.mp4": "failed"}
    broken = document["report"]["items"][2]
    assert broken["error_type"] == "VideoOpenError" and broken["hint"]
    assert document["run_lists"]["failed"].endswith("_sheet_failed.txt")
    # Everything for people went to stderr.
    assert "Done: 1 succeeded" in captured.err and "3 videos" in captured.err


def test_json_dry_run_and_errors(folder, tmp_path, capsys):
    import json

    assert cli.main(["sheet", str(folder), "--dry-run", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert [item["state"] for item in document["plan"]["items"]] == ["pending"] * 3
    assert "report" not in document

    missing = str(tmp_path / "missing.mp4")
    assert cli.main(["sheet", missing, "--json"]) == 2
    document = json.loads(capsys.readouterr().out)
    assert document["error"]["type"] == "UsageError"
    assert document["error"]["message"].startswith("Not found")
    assert document["exit_code"] == 2


def test_open_shows_the_output_folder(folder, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("videoconsole.desktop.open_folder", opened.append)
    out = tmp_path / "out"
    args = ["sheet", str(folder), "-w", "1", "-o", str(out), "--open"]
    assert cli.main(args) == 1  # broken.mp4 fails, the others are there to look at
    assert opened == [out]

    sheet = tmp_path / "single" / "s.jpg"
    sheet.parent.mkdir()
    video = folder / "a.mp4"
    assert cli.main(["sheet", str(video), "-o", str(sheet), "--open"]) == 0
    assert opened == [out, sheet.parent]


def test_console_section_of_the_config(clip, tmp_path, monkeypatch):
    from videoconsole import ConsoleOptions, Desktop

    config = tmp_path / "c.yaml"
    config.write_text("console:\n  notify_after: 0\n  keep_awake: false\n", encoding="utf-8")
    seen = []
    real = Desktop.configure
    monkeypatch.setattr(Desktop, "configure", lambda self, o: seen.append(o) or real(self, o))
    args = ["sheet", str(clip), "-o", str(tmp_path / "s.jpg")]
    assert cli.main([*args, "--config", str(config)]) == 0
    assert seen == [ConsoleOptions(notify_after=0, keep_awake=False)]


def test_invalid_arguments_pause_when_launched_alone(monkeypatch):
    paused = []
    monkeypatch.setattr(cli, "pause_if_alone", lambda: paused.append(True))
    with pytest.raises(SystemExit):
        cli.main(["sheet", "--bogus"])
    assert paused == [True]


def test_no_progress_is_accepted(clip, tmp_path):
    out = tmp_path / "s.jpg"
    assert cli.main(["sheet", str(clip), "-o", str(out), "--no-progress"]) == 0
    assert out.is_file()
