import json

import yaml

from videocore.state import default_state_path
from videothumb import cli
from videothumb.config import CONFIG
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions


def options_for(argv, text=None, tmp_path=None):
    values = None
    if text is not None:
        path = tmp_path / "c.yaml"
        path.write_text(text, encoding="utf-8")
        values = CONFIG.load(path)
    return cli.build_options(cli.build_parser().parse_args(argv), values)


def test_config_values_apply_and_flags_override_them(clip, tmp_path):
    text = "sheet:\n  columns: 3\n  jpeg_quality: 70\n  sampling: precise\nframes:\n  count: 4\n"
    sheet = ["sheet", str(clip)]
    assert options_for(sheet, text, tmp_path) == SheetOptions(
        columns=3, jpeg_quality=70, sampling="precise"
    )
    assert options_for([*sheet, "--columns", "6"], text, tmp_path).columns == 6
    snaps = ["frames", str(clip)]
    assert options_for(snaps, text, tmp_path) == SnapshotOptions(count=4)
    assert options_for([*snaps, "--precise"], text, tmp_path).sampling == "precise"


def test_run_section_sets_how_folders_are_processed(folder, tmp_path, capsys):
    (folder / "sub").mkdir()
    (folder / "sub" / "c.mp4").write_bytes((folder / "a.mp4").read_bytes())
    config = tmp_path / "c.yaml"
    config.write_text("run:\n  recursive: true\n  workers: 1\n", encoding="utf-8")
    args = ["sheet", str(folder), "--dry-run", "--config", str(config)]

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "4 videos: 4 to process" in out
    assert "with 1 in parallel" in out

    assert cli.main([*args, "--no-recursive"]) == 0
    assert "3 videos: 3 to process" in capsys.readouterr().out


def test_invalid_config_is_a_usage_error(clip, tmp_path, capsys):
    config = tmp_path / "c.yaml"
    config.write_text("sheet:\n  columns: 0\n", encoding="utf-8")
    args = ["sheet", str(clip), "--config", str(config)]
    assert cli.main(args) == 2
    assert "sheet: 'columns' must be greater than 0" in capsys.readouterr().err

    config.write_text("run:\n  output: somewhere\n", encoding="utf-8")
    assert cli.main(args) == 2
    assert "unknown option 'output'" in capsys.readouterr().err

    config.write_text("snapshots:\n  count: 3\n", encoding="utf-8")
    assert cli.main(args) == 2
    assert "unknown section 'snapshots': it is now 'frames:'" in capsys.readouterr().err


def test_config_commands(clip, tmp_path, capsys):
    assert cli.main(["config", "path"]) == 0
    assert "not created yet" in capsys.readouterr().out

    assert cli.main(["config", "set", "sheet.columns", "2"]) == 0
    assert cli.main(["config", "set", "sheet.rows", "2"]) == 0
    assert cli.main(["config", "set", "jpeg_quality", "80"]) == 2  # in two sections
    assert "use one of sheet.jpeg_quality, frames.jpeg_quality" in capsys.readouterr().err

    assert cli.main(["config", "show"]) == 0
    shown = yaml.safe_load(capsys.readouterr().out)
    assert shown["sheet"]["columns"] == 2 and shown["frames"]["count"] == 10
    assert "output" not in shown["run"]

    # The CLI picks the new defaults up.
    assert cli.main(["sheet", str(clip), "--dry-run"]) == 0
    out = tmp_path / "s.jpg"
    assert cli.main(["sheet", str(clip), "-o", str(out)]) == 0
    assert cli.main(["config", "unset", "sheet.columns"]) == 0
    assert "default applies (5)" in capsys.readouterr().out


def test_runs_learn_how_fast_the_task_is(folder):
    assert cli.main(["sheet", str(folder), "-w", "1"]) == 1
    speeds = json.loads(default_state_path("videothumb").read_text(encoding="utf-8"))["speed"]
    assert set(speeds) == {"sheet"} and speeds["sheet"] > 0
