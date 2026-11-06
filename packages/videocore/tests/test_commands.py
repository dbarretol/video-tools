from pathlib import Path

import pytest

from videocore.commands import (
    config_argument,
    configured,
    help_epilog,
    with_default_task,
    with_help,
)
from videocore.config import AppConfig, Section
from videocore.options import option


def default():
    return "sheet"


def unused():
    raise AssertionError("not needed")


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["clip.mp4"], ["sheet", "clip.mp4"]),
        (["-o", "out", "clip.mp4"], ["sheet", "-o", "out", "clip.mp4"]),
        (["-v", "clip.mp4"], ["-v", "sheet", "clip.mp4"]),
        ([r".\sheet"], ["sheet", r".\sheet"]),
    ],
)
def test_the_default_task_goes_first(argv, expected):
    assert with_default_task(argv, ["sheet", "frames"], default, global_flags=["-v"]) == expected


@pytest.mark.parametrize(
    "argv", [[], ["-v"], ["--help"], ["--version"], ["frames", "x"], ["-v", "sheet", "x"]]
)
def test_commands_and_help_are_left_alone(argv):
    assert with_default_task(argv, ["sheet", "frames"], unused, global_flags=["-v"]) == argv


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["help"], ["--help"]),
        (["/?"], ["--help"]),
        (["-?"], ["--help"]),
        (["/help"], ["--help"]),
        (["help", "frames"], ["frames", "--help"]),
        (["help", "nothing"], ["--help"]),
        (["-v", "help", "sheet"], ["-v", "sheet", "--help"]),
        (["sheet", "/?"], ["sheet", "--help"]),
        (["sheet", "help"], ["sheet", "help"]),  # a file named help
        (["clip.mp4"], ["clip.mp4"]),
    ],
)
def test_other_ways_of_asking_for_help(argv, expected):
    assert with_help(argv, ["sheet", "frames"], global_flags=["-v"]) == expected


def test_help_epilog_aligns_and_wraps():
    text = help_epilog(
        [("tool a.mp4", "short"), ("tool b.mp4 " + "x" * 40, "below its command")],
        "A note " * 20,
    )
    lines = text.splitlines()
    assert lines[:4] == [
        "examples:",
        "  tool a.mp4  short",
        "  tool b.mp4 " + "x" * 40,
        "              below its command",
    ]
    assert lines[4] == "" and all(len(line) <= 79 for line in lines)


def test_config_argument():
    assert config_argument(["x", "--config", "a.yaml"]) == Path("a.yaml")
    assert config_argument(["--config=b.yaml", "x"]) == Path("b.yaml")
    assert config_argument(["x", "--config"]) is None and config_argument(["x"]) is None


def test_configured_falls_back_to_the_built_in_default(tmp_path, monkeypatch):
    from dataclasses import dataclass
    from typing import Literal

    @dataclass(frozen=True)
    class Task:
        default_task: Literal["a", "b"] = option("a", "task", cli=False)

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = AppConfig("tool", (Section("console", (Task,)),), "test")
    good, bad = tmp_path / "good.yaml", tmp_path / "bad.yaml"
    good.write_text("console:\n  default_task: b\n", encoding="utf-8")
    bad.write_text("console:\n  default_task: c\n", encoding="utf-8")
    assert configured(config, ["--config", str(good)], "console", "default_task") == "b"
    assert configured(config, ["--config", str(bad)], "console", "default_task") == "a"
    assert configured(config, ["--config", "missing.yaml"], "console", "default_task") == "a"
    assert configured(config, [], "console", "default_task") == "a"
