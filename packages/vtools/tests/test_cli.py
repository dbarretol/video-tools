import pytest

from vtools import cli


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.thumb, "main", lambda argv: seen.append(("thumb", argv)) or 0)
    monkeypatch.setattr(cli.vsr, "main", lambda argv: seen.append(("vsr", argv)) or 0)
    return seen


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["sheet", "a.mp4"], ("thumb", ["sheet", "a.mp4"])),
        (["pack", "a.mp4", "-w", "2"], ("thumb", ["pack", "a.mp4", "-w", "2"])),
        (["gui"], ("thumb", ["gui"])),
        (["archive", "a.mp4"], ("vsr", ["archive", "a.mp4"])),
        (["-v", "status", "d"], ("vsr", ["status", "-v", "d"])),
        (["-q", "frames", "d"], ("thumb", ["frames", "-q", "d"])),
        (["a.mp4", "-o", "x"], ("thumb", ["a.mp4", "-o", "x"])),  # its default task
        (["-v", "a.mp4"], ("thumb", ["a.mp4", "-v"])),
        (["process", "a.mp4"], ("vsr", ["process", "a.mp4"])),
        (["snapshots", "a.mp4"], ("thumb", ["snapshots", "a.mp4"])),
        (
            ["config", "vsr", "set", "num_snapshots", "9"],
            ("vsr", ["config", "set", "num_snapshots", "9"]),
        ),
        (["config", "thumb", "show"], ("thumb", ["config", "show"])),
    ],
)
def test_each_command_goes_to_its_tool(calls, argv, expected):
    assert cli.main(argv) == 0
    assert calls == [expected]


def test_help_and_version(calls, capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "archive" in out and "pack" in out and "default task" in out
    assert cli.main(["--version"]) == 0
    assert "videothumb" in capsys.readouterr().out
    assert calls == []


@pytest.mark.parametrize("argv", [["help"], ["/?"], ["-v", "-?"]])
def test_help_in_other_words(calls, capsys, argv):
    assert cli.main(argv) == 0
    assert "examples:" in capsys.readouterr().out
    assert calls == []


def test_help_of_a_command_goes_to_its_tool(calls):
    assert cli.main(["help", "archive"]) == 0
    assert cli.main(["help", "sheet"]) == 0
    assert calls == [("vsr", ["archive", "--help"]), ("thumb", ["sheet", "--help"])]


def test_config_needs_a_tool(calls, capsys):
    assert cli.main(["config", "show"]) == 2
    assert "vtools config {thumb,vsr}" in capsys.readouterr().err


def test_a_real_run(clip, tmp_path, capsys):
    out = tmp_path / "s.jpg"
    assert cli.main([str(clip), "-o", str(out)]) == 0
    assert out.is_file() and "Contact sheet saved" in capsys.readouterr().out
    archive = tmp_path / "archive"
    args = ["archive", str(clip), "-o", str(archive), "-n", "4", "--grid", "2x2"]
    assert cli.main(["-q", *args]) == 0
    assert cli.main(["status", str(clip), "-o", str(archive)]) == 0
