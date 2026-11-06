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
    assert "1 succeeded, 1 failed, 1 skipped" in capsys.readouterr().out
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

    monkeypatch.setattr(cli, "process", interrupted)
    assert cli.main(["--video", str(clip), "--mode", "sheet"]) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_mode_is_required(clip):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--video", str(clip)])
    assert exc.value.code == 2
