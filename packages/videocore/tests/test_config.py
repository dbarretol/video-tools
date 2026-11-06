import argparse
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from videocore import RunOptions
from videocore import config as config_module
from videocore.config import AppConfig, ConfigError, Section, add_config_commands
from videocore.options import option, validate


@dataclass(frozen=True)
class Sheet:
    columns: int = option(5, "thumbnails per row", gt=0)
    quality: int = option(90, "JPEG quality", ge=1, le=100)

    def __post_init__(self) -> None:
        validate(self)


@dataclass(frozen=True)
class Snaps:
    count: int = option(10, "snapshots to save", gt=0)
    quality: int = option(90, "JPEG quality", ge=1, le=100)
    first: int = option(0, "first snapshot", ge=0)

    def __post_init__(self) -> None:
        validate(self)
        if self.first >= self.count:
            raise ValueError("first must be smaller than count")


CONFIG = AppConfig(
    "demo",
    (
        Section("sheet", (Sheet,), "--mode sheet"),
        Section("snaps", (Snaps,)),
        Section("run", (RunOptions,), skip=("recursive",)),
    ),
    "demo defaults.",
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    monkeypatch.delenv("DEMO_CONFIG", raising=False)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_template_has_every_section_and_reads_back_as_the_defaults(tmp_path):
    text = CONFIG.render()
    assert text.startswith("# demo defaults.\n")
    assert "sheet:  # --mode sheet\n  columns: 5  # thumbnails per row\n" in text
    assert "recursive" not in text  # skipped in its section
    assert "# videos processed in parallel for folders; null = automatic" in text
    path = write(tmp_path / "c.yaml", text)
    assert CONFIG.load(path) == {
        "sheet": {"columns": 5, "quality": 90},
        "snaps": {"count": 10, "quality": 90, "first": 0},
        "run": {"output": None, "workers": None},
    }


def test_missing_and_empty_files_have_no_values(tmp_path):
    empty = {"sheet": {}, "snaps": {}, "run": {}}
    assert CONFIG.load(None) == empty
    assert CONFIG.load(write(tmp_path / "c.yaml", "# nothing yet\n")) == empty


def test_relative_paths_are_relative_to_the_file(tmp_path):
    path = write(tmp_path / "c.yaml", "run:\n  output: out\n")
    assert CONFIG.load(path)["run"]["output"] == tmp_path / "out"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("other:\n  x: 1\n", "unknown section 'other'; expected the sections 'sheet:', 'snaps:'"),
        ("[1, 2]\n", "expected the sections"),
        ("sheet: 3\n", "sheet: 'sheet' must be a mapping"),
        ("sheet:\n  count: 3\n", "sheet: unknown option 'count'"),
        ("sheet:\n  columns: 0\n", "sheet: 'columns' must be greater than 0, got 0"),
        ("run:\n  recursive: true\n", "run: unknown option 'recursive'"),
        ("snaps:\n  count: 2\n  first: 5\n", "snaps: first must be smaller than count"),
        ("sheet:\n  columns: [\n", "Could not read"),
    ],
)
def test_invalid_files_name_the_problem(tmp_path, text, message):
    path = write(tmp_path / "c.yaml", text)
    with pytest.raises(ConfigError, match=message.replace("[", r"\[")) as raised:
        CONFIG.load(path)
    assert str(path) in str(raised.value)


RENAMED = AppConfig(
    "demo",
    (Section("sheet", (Sheet,), renamed=(("cols", "columns"),)), Section("snaps", (Snaps,))),
    "demo defaults.",
    renamed=(("snapshots", "snaps"),),
)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("snapshots:\n  count: 3\n", "unknown section 'snapshots': it is now 'snaps:'"),
        ("sheet:\n  cols: 3\n", "sheet: unknown option 'cols': it is now 'columns'"),
    ],
)
def test_old_names_get_their_new_name(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        RENAMED.load(write(tmp_path / "c.yaml", text))


def test_set_with_an_old_name_gives_the_new_one(tmp_path):
    with pytest.raises(ConfigError, match="'cols': it is now 'columns'"):
        RENAMED.set_value(tmp_path / "c.yaml", "cols", "3")
    assert not (tmp_path / "c.yaml").exists()


def test_find_and_target_follow_the_lookup_order(tmp_path, monkeypatch):
    default = CONFIG.default_path()
    assert default == tmp_path / "appdata" / "demo" / "config.yaml"
    assert CONFIG.find() is None
    assert CONFIG.target() == default
    with pytest.raises(ConfigError, match="not found"):
        CONFIG.find(tmp_path / "missing.yaml")

    env = write(tmp_path / "env.yaml", "")
    monkeypatch.setenv("DEMO_CONFIG", str(env))
    assert CONFIG.env_var == "DEMO_CONFIG"
    assert CONFIG.find() == env
    assert CONFIG.target() == env
    assert CONFIG.target(tmp_path / "x.yaml") == tmp_path / "x.yaml"


def test_keys_resolve_with_or_without_their_section():
    assert CONFIG.resolve_key("columns")[1].name == "columns"
    assert CONFIG.resolve_key("snaps.quality")[0].name == "snaps"
    with pytest.raises(ConfigError, match="several sections; use one of sheet.quality"):
        CONFIG.resolve_key("quality")
    with pytest.raises(ConfigError, match="Unknown option 'bogus'"):
        CONFIG.resolve_key("bogus")
    with pytest.raises(ConfigError, match="Unknown section 'nope'"):
        CONFIG.resolve_key("nope.columns")
    with pytest.raises(ConfigError, match="Unknown option 'recursive' in run"):
        CONFIG.resolve_key("run.recursive")
    assert "run.workers" in CONFIG.keys()


def test_set_changes_one_line_and_keeps_the_rest(tmp_path):
    text = (
        "# my notes\n"
        "sheet:\n"
        "    columns: 5   # I like five\n"
        "    quality: 90\n"
        "\n"
        "# snapshots below\n"
        "snaps:\n"
        "  count: 10\n"
    )
    path = write(tmp_path / "c.yaml", text)

    assert CONFIG.set_value(path, "sheet.columns", "6") == ("sheet.columns", 6)
    assert CONFIG.set_value(path, "count", "20") == ("snaps.count", 20)

    assert path.read_text(encoding="utf-8") == text.replace("columns: 5 ", "columns: 6 ").replace(
        "count: 10", "count: 20"
    )


def test_set_adds_missing_entries_and_sections(tmp_path):
    path = write(tmp_path / "c.yaml", "sheet:\n  columns: 5\n\n# end\n")
    CONFIG.set_value(path, "sheet.quality", "80")
    CONFIG.set_value(path, "run.workers", "2")
    assert path.read_text(encoding="utf-8") == (
        "sheet:\n  columns: 5\n  quality: 80  # JPEG quality\n\n# end\n\n"
        "run:\n  workers: 2  # videos processed in parallel for folders; null = automatic\n"
    )
    assert CONFIG.load(path)["run"] == {"workers": 2}


def test_set_creates_the_file_from_the_template(tmp_path):
    path = tmp_path / "new" / "config.yaml"
    CONFIG.set_value(path, "snaps.count", "3")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# demo defaults.")
    assert "  count: 3  # snapshots to save\n" in text
    assert CONFIG.load(path)["snaps"]["count"] == 3


def test_invalid_values_are_not_written(tmp_path):
    path = write(tmp_path / "c.yaml", "snaps:\n  count: 2\n")
    before = path.read_text(encoding="utf-8")
    for key, value, message in [
        ("sheet.columns", "0", "'columns' must be greater than 0"),
        ("sheet.columns", "many", "'columns' must be int"),
        ("snaps.first", "4", "first must be smaller than count"),
    ]:
        with pytest.raises(ConfigError, match=message):
            CONFIG.set_value(path, key, value)
    assert path.read_text(encoding="utf-8") == before
    assert list(tmp_path.iterdir()) == [path]  # no temporary file left


def test_set_can_fix_an_invalid_file(tmp_path):
    path = write(tmp_path / "c.yaml", "sheet:\n  columns: 0\n")
    CONFIG.set_value(path, "sheet.columns", "4")
    assert CONFIG.load(path)["sheet"] == {"columns": 4}


def test_set_paths_are_relative_to_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write(tmp_path / "sub.yaml", "")
    key, value = CONFIG.set_value(tmp_path / "conf" / "c.yaml", "run.output", "archive")
    assert value == tmp_path / "archive"
    assert CONFIG.load(tmp_path / "conf" / "c.yaml")["run"]["output"] == tmp_path / "archive"
    CONFIG.set_value(path, "run.workers", "null")
    assert CONFIG.load(path)["run"] == {"workers": None}


def test_unset_removes_the_line(tmp_path):
    path = write(tmp_path / "c.yaml", "sheet:\n  columns: 6  # mine\n  quality: 80\n")
    assert CONFIG.unset_value(path, "sheet.columns") == ("sheet.columns", True)
    assert path.read_text(encoding="utf-8") == "sheet:\n  quality: 80\n"
    assert CONFIG.unset_value(path, "sheet.columns") == ("sheet.columns", False)
    assert CONFIG.unset_value(tmp_path / "missing.yaml", "count") == ("snaps.count", False)


def test_files_in_another_layout_are_rewritten_from_the_template(tmp_path):
    path = write(tmp_path / "c.yaml", "sheet: {columns: 6}\n")
    CONFIG.set_value(path, "sheet.quality", "70")
    values = CONFIG.load(path)
    assert values["sheet"] == {"columns": 6, "quality": 70}
    assert "# thumbnails per row" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("rest", "comment"),
    [
        (" 5  # five", "  # five"),
        (" 5", ""),
        (" 'a # b'  # c", "  # c"),
        (" 'a # b'", ""),
        (" [1, 2] # list", " # list"),
    ],
)
def test_trailing_comments_are_found(rest, comment):
    assert config_module._trailing_comment(rest) == comment


def run_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo config")
    add_config_commands(parser, CONFIG)
    args = parser.parse_args(argv)
    return args.handler(args)


def test_commands_manage_the_file(tmp_path, capsys, monkeypatch):
    default = CONFIG.default_path()
    assert run_command(["path"]) == 0
    assert "not created yet" in capsys.readouterr().out

    assert run_command(["set", "sheet.columns", "7"]) == 0
    assert capsys.readouterr().out == f"sheet.columns = 7 (in {default})\n"
    assert run_command(["show"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"# Source: {default}\n")
    assert yaml.safe_load(out)["sheet"]["columns"] == 7

    assert run_command(["unset", "columns"]) == 0
    assert "removed" in capsys.readouterr().out
    assert run_command(["unset", "columns"]) == 0
    assert "was not set; the built-in default applies (5)" in capsys.readouterr().out

    with pytest.raises(ConfigError, match="already exists"):
        run_command(["init"])
    assert run_command(["init", "--force"]) == 0

    opened = []
    monkeypatch.setattr(config_module, "open_in_editor", opened.append)
    other = tmp_path / "other.yaml"
    assert run_command(["edit", "--config", str(other)]) == 0
    assert opened == [other]
    assert other.read_text(encoding="utf-8") == CONFIG.render()


def test_show_reports_errors_before_printing(tmp_path, capsys):
    path = write(tmp_path / "c.yaml", "sheet:\n  columns: 0\n")
    with pytest.raises(ConfigError):
        run_command(["show", "--config", str(path)])
    assert capsys.readouterr().out == ""


def test_editor_comes_from_the_environment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(config_module.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setenv("VISUAL", "code --wait")
    config_module.open_in_editor(tmp_path / "c.yaml")
    assert calls == [["code", "--wait", str(tmp_path / "c.yaml")]]
