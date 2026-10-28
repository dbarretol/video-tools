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


def test_missing_video(tmp_path, capsys):
    assert cli.main(["--video", str(tmp_path / "missing.mp4"), "--mode", "sheet"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_invalid_option_value(clip, capsys):
    assert cli.main(["--video", str(clip), "--mode", "sheet", "--columns", "0"]) == 1
    assert "columns" in capsys.readouterr().err


def test_mode_is_required(clip):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--video", str(clip)])
    assert exc.value.code == 2
