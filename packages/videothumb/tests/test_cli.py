import pytest

from videothumb import cli


def test_without_arguments_opens_the_gui(monkeypatch):
    launched = []
    monkeypatch.setattr("videothumb.gui.app.launch", lambda: launched.append(True))
    assert cli.main([]) == 0
    assert launched == [True]


def test_sheet_for_one_video(clip, tmp_path, capsys):
    out = tmp_path / "sheet.jpg"
    args = [
        "--video",
        str(clip),
        "--mode",
        "sheet",
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


def test_snapshots_for_one_video(clip, tmp_path):
    out = tmp_path / "snaps"
    args = [
        "--video",
        str(clip),
        "--mode",
        "snapshots",
        "--count",
        "4",
        "--precise",
        "-o",
        str(out),
    ]
    assert cli.main(args) == 0
    assert len(list(out.glob("snapshot_*.jpg"))) == 4


def test_folder_reports_failures(folder, tmp_path, capsys):
    out = tmp_path / "out"
    args = [
        "--folder",
        str(folder),
        "--mode",
        "snapshots",
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
    assert (out / "a").is_dir() and (out / "b").is_dir()


def test_empty_folder(tmp_path, capsys):
    assert cli.main(["--folder", str(tmp_path), "--mode", "sheet"]) == 0
    assert "No videos found" in capsys.readouterr().out


def test_missing_video_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["--video", str(tmp_path / "missing.mp4"), "--mode", "sheet"]) == 2
    assert "Video not found" in capsys.readouterr().err


def test_missing_folder_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["--folder", str(tmp_path / "missing"), "--mode", "sheet"]) == 2
    assert "Folder not found" in capsys.readouterr().err


def test_broken_video_fails(tmp_path, capsys):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert cli.main(["--video", str(broken), "--mode", "sheet"]) == 1


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
        cli.main(["--video", str(clip), "--mode", "sheet", option, value])
    assert exc.value.code == 2
    assert option in capsys.readouterr().err


def test_skip_existing_leaves_outputs_alone(folder, capsys):
    (folder / "a_sheet.jpg").write_bytes(b"from an earlier run")
    args = ["--folder", str(folder), "--mode", "sheet", "-w", "1", "--skip-existing"]

    assert cli.main(args) == 1  # broken.mp4 still fails
    assert "1 succeeded, 1 skipped (output already exists), 1 failed." in capsys.readouterr().out
    assert (folder / "a_sheet.jpg").read_bytes() == b"from an earlier run"
    assert (folder / "b_sheet.jpg").is_file()


def test_outputs_are_overwritten_by_default(clip, tmp_path, capsys):
    out = tmp_path / "sheet.jpg"
    out.write_bytes(b"from an earlier run")
    assert cli.main(["--video", str(clip), "--mode", "sheet", "-o", str(out)]) == 0
    assert out.read_bytes() != b"from an earlier run"

    args = ["--video", str(clip), "--mode", "sheet", "-o", str(out), "--skip-existing"]
    before = out.read_bytes()
    assert cli.main(args) == 0
    assert "Skipped" in capsys.readouterr().out
    assert out.read_bytes() == before


def test_overwrite_and_skip_existing_are_exclusive(clip):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--video", str(clip), "--mode", "sheet", "--overwrite", "--skip-existing"])
    assert exc.value.code == 2


def test_ctrl_c_exits_with_130(clip, monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("videothumb.jobs.make_sheet", interrupted)
    assert cli.main(["--video", str(clip), "--mode", "sheet"]) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_mode_is_required(clip):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--video", str(clip)])
    assert exc.value.code == 2


def test_flags_of_the_other_mode_are_reported(clip, tmp_path, caplog):
    out = tmp_path / "sheet.jpg"
    args = ["--video", str(clip), "--mode", "sheet", "--count", "3", "-o", str(out)]
    assert cli.main(args) == 0
    assert "--count does not apply to --mode sheet; ignored" in caplog.text


def test_options_share_one_declaration():
    from videothumb.sheet import SheetOptions
    from videothumb.snapshots import SnapshotOptions

    args = cli.build_parser().parse_args(["--video", "x", "--mode", "snapshots", "--quality", "70"])
    options = cli.build_options(args)
    assert options == SnapshotOptions(jpeg_quality=70)
    assert cli.build_options(
        cli.build_parser().parse_args(["--video", "x", "--mode", "sheet"])
    ) == (SheetOptions())


def test_dry_run_lists_what_would_happen(folder, capsys):
    (folder / "a_sheet.jpg").write_bytes(b"from an earlier run")
    args = ["--folder", str(folder), "--mode", "sheet", "--skip-existing", "--dry-run"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "3 videos: 2 to process, 1 already done" in out
    assert "[skip, already done] a.mp4 -> a_sheet.jpg" in out
    assert not (folder / "b_sheet.jpg").exists()


def test_batch_prints_the_plan_first(folder, capsys):
    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1"]) == 1
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
    args = ["--video", str(clip), "--mode", "sheet", "-o", str(tmp_path / "s.jpg")]
    assert cli.main(args) == 3
    err = capsys.readouterr().err
    assert "incomplete output: contact sheet: only 10/20 frames could be decoded" in err
    assert cli.main([*args, "--strict"]) == 1


def test_batch_reports_incomplete_outputs(folder, monkeypatch, capsys):
    lose_frames(monkeypatch, keep=1)
    (folder / "broken.mp4").unlink()
    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1"]) == 3
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
    ("mode", "function"), [("sheet", "sample_frames"), ("snapshots", "iter_frames")]
)
def test_keyframe_sampling_falls_back_to_precise(clip, tmp_path, monkeypatch, mode, function):
    import importlib

    module = importlib.import_module(f"videothumb.{mode}")
    real, calls = getattr(module, function), []

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, function, spy)
    assert cli.main(["--video", str(clip), "--mode", mode, "-o", str(tmp_path / "out")]) == 0
    assert calls[0]["fallback"] is True and calls[0]["mode"] == "keyframe"


def test_failures_come_with_a_hint_and_the_log_path(folder, tmp_path, capsys):
    broken = folder / "broken.mp4"
    assert cli.main(["--video", str(broken), "--mode", "sheet"]) == 1
    err = capsys.readouterr().err
    assert "Hint: the file may be damaged" in err
    assert "Details: " in err and "videothumb" in err

    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1"]) == 1
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
    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1"]) == 130
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

    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1"]) == 1
    assert "Videos to retry are listed in" in capsys.readouterr().out
    (failed,) = default_runs_dir("videothumb").glob("*_sheet_failed.txt")
    assert str((folder / "broken.mp4").resolve()) in failed.read_text(encoding="utf-8-sig")


def test_quiet_leaves_only_what_needs_attention(folder, capsys):
    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1", "-q"]) == 1
    captured = capsys.readouterr()
    assert "to process" not in captured.out and "Done:" not in captured.out
    assert "[1/3]" not in captured.out
    assert "Videos to retry are listed in" in captured.out
    assert "  - broken.mp4: VideoOpenError" in captured.err

    (folder / "broken.mp4").unlink()
    assert cli.main(["--folder", str(folder), "--mode", "sheet", "-w", "1", "-q"]) == 0
    assert capsys.readouterr() == ("", "")


def test_names_with_brackets_are_printed_as_they_are(clip, tmp_path, capsys):
    import shutil

    video = tmp_path / "clip [1080p] [bold]x.mp4"
    shutil.copy(clip, video)
    assert cli.main(["--video", str(video), "--mode", "sheet"]) == 0
    assert "clip [1080p] [bold]x_sheet.jpg" in capsys.readouterr().out
