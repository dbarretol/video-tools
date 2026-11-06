from pathlib import Path

import pytest
import yaml

from videocore import RunOptions, State
from videosemanticreducer.cli import main
from videosemanticreducer.config import (
    CONFIG_ENV,
    ConfigError,
    ProcessDefaults,
    default_config_path,
    find_config,
    load_config,
    render_template,
)
from videosemanticreducer.pipeline import ProcessOptions


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_template_round_trips_to_the_builtin_defaults(tmp_path):
    path = write(tmp_path / "config.yaml", render_template())
    assert load_config(path) == ProcessDefaults()
    assert "# snapshots in the summary video" in path.read_text(encoding="utf-8")


def test_partial_config_keeps_other_defaults(tmp_path):
    path = write(tmp_path / "c.yaml", "process:\n  num_snapshots: 60\n  summary: false\n")
    assert load_config(path) == ProcessDefaults(
        options=ProcessOptions(num_snapshots=60, summary=False)
    )


def test_empty_file_and_no_file_give_defaults(tmp_path):
    assert load_config(write(tmp_path / "c.yaml", "")) == ProcessDefaults()
    assert load_config(None) == ProcessDefaults()


def test_relative_output_is_relative_to_the_config_file(tmp_path):
    path = write(tmp_path / "c.yaml", "process:\n  output: archive\n")
    assert load_config(path).run.output == tmp_path / "archive"


def test_integer_snap_duration_is_accepted(tmp_path):
    path = write(tmp_path / "c.yaml", "process:\n  snap_duration: 1\n")
    assert load_config(path).options.snap_duration == 1.0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("process:\n  snapshots: 10\n", "unknown option 'snapshots'"),
        ("process:\n  num_snapshots: many\n", "'num_snapshots' must be int"),
        ("process:\n  num_snapshots: true\n", "'num_snapshots' must be int"),
        ("process:\n  summary: 1\n", "'summary' must be bool"),
        ("other:\n  x: 1\n", "single 'process:' section"),
        ("process: [1, 2]\n", "must be a mapping"),
        ("process:\n  num_snapshots: [\n", "Could not read"),
        ("process:\n  num_snapshots: 0\n", "'num_snapshots' must be greater than 0, got 0"),
        ("process:\n  workers: 0\n", "'workers' must be greater than 0"),
        ("process:\n  output: 3\n", "'output' must be a path or null"),
    ],
)
def test_invalid_config(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path / "c.yaml", text))


def test_find_config_order(tmp_path, monkeypatch):
    assert find_config() is None  # no default file yet (see conftest)

    default = default_config_path()
    default.parent.mkdir(parents=True)
    write(default, "")
    assert find_config() == default

    env_file = write(tmp_path / "env.yaml", "")
    monkeypatch.setenv(CONFIG_ENV, str(env_file))
    assert find_config() == env_file

    explicit = write(tmp_path / "explicit.yaml", "")
    assert find_config(explicit) == explicit


def test_missing_explicit_or_env_config_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(ConfigError):
        find_config(tmp_path / "missing.yaml")
    monkeypatch.setenv(CONFIG_ENV, str(tmp_path / "missing.yaml"))
    with pytest.raises(ConfigError):
        find_config()


def test_cli_config_init_show_and_path(capsys):
    assert main(["config", "path"]) == 0
    assert "not created yet" in capsys.readouterr().out

    assert main(["config", "init"]) == 0
    assert default_config_path().is_file()
    assert main(["config", "init"]) == 2  # refuses to overwrite
    assert main(["config", "init", "--force"]) == 0
    capsys.readouterr()

    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert f"# Source: {default_config_path()}" in out
    assert yaml.safe_load(out)["process"]["num_snapshots"] == 150


def test_process_uses_config_and_flags_override_it(clip, tmp_path):
    out = tmp_path / "out"
    config = write(
        tmp_path / "c.yaml",
        f"process:\n  output: {out.as_posix()}\n  summary: false\n  fingerprint: false\n"
        "  thumb_cols: 2\n  thumb_rows: 2\n",
    )
    assert main(["process", str(clip), "--config", str(config)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "clip.archive.json",
        "clip_info.txt",
        "clip_thumb.jpg",
    ]

    # --fingerprint on the command line beats `fingerprint: false` in the file.
    assert main(["process", str(clip), "--config", str(config), "--fingerprint"]) == 0
    assert (out / "clip.fingerprint.json").is_file()

    # Each run teaches how fast archiving is here, for the next estimates.
    assert State("videosemanticreducer").speed("archive") != 1.0


def test_process_with_invalid_config_fails_cleanly(clip, tmp_path):
    config = write(tmp_path / "c.yaml", "process:\n  bogus: 1\n")
    assert main(["process", str(clip), "--config", str(config)]) == 2


@pytest.mark.parametrize("setting", ["num_snapshots: 0", "workers: 0", "thumb_cols: -2"])
def test_process_with_out_of_range_config_values_is_a_usage_error(clip, tmp_path, setting):
    config = write(tmp_path / "c.yaml", f"process:\n  {setting}\n")
    assert main(["process", str(clip), "--config", str(config)]) == 2


def test_template_lists_every_option_once():
    keys = yaml.safe_load(render_template())["process"]
    expected = [f.name for f in ProcessOptions.__dataclass_fields__.values() if f.metadata]
    assert list(keys) == expected + ["output", "recursive", "workers"]
    assert "# videos processed in parallel for folders; null = automatic" in render_template()


def test_run_defaults_are_validated():
    with pytest.raises(ValueError, match="workers must be greater than 0"):
        RunOptions(workers=0)


def test_cli_config_set_and_unset_keep_the_template_comments(capsys):
    assert main(["config", "init"]) == 0
    assert main(["config", "set", "snap_duration", "2"]) == 0
    assert "process.snap_duration = 2.0" in capsys.readouterr().out
    assert load_config(default_config_path()).options.snap_duration == 2.0
    text = default_config_path().read_text(encoding="utf-8")
    assert "  snap_duration: 2.0  # " in text

    assert main(["config", "set", "num_snapshots", "0"]) == 2  # invalid: nothing written
    assert main(["config", "set", "bogus", "1"]) == 2
    assert default_config_path().read_text(encoding="utf-8") == text

    assert main(["config", "unset", "snap_duration"]) == 0
    default = ProcessOptions().snap_duration
    assert load_config(default_config_path()).options.snap_duration == default


def test_cli_config_edit_creates_the_file_and_opens_it(monkeypatch):
    opened = []
    monkeypatch.setattr("videocore.config.open_in_editor", opened.append)
    assert main(["config", "edit"]) == 0
    assert opened == [default_config_path()]
    assert load_config(default_config_path()) == ProcessDefaults()
