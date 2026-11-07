from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

from videocore.options import (
    add_arguments,
    given_values,
    option,
    options_of,
    parse_mapping,
    validate,
)


@dataclass(frozen=True)
class Task:
    count: int = option(10, "how many", flags=("-n", "--count"), gt=0, group="main")
    quality: int = option(90, "JPEG quality", ge=1, le=100)
    seconds: float = option(0.5, "seconds per frame", gt=0)
    precise: bool = option(False, "exact positions")
    mode: Literal["fast", "slow"] = option("fast", "how to decode")
    output: Path | None = option(None, "where to write", default_text="next to the video")
    internal: int | None = field(default=None)
    config_only: int = option(3, "not a flag", cli=False)

    def __post_init__(self) -> None:
        validate(self)


def parser_for(*classes: type) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(exit_on_error=False)
    add_arguments(parser, *classes)
    return parser


def test_options_resolve_types_and_skip_plain_fields():
    by_name = {opt.name: opt for opt in options_of(Task)}
    assert list(by_name) == [
        "count",
        "quality",
        "seconds",
        "precise",
        "mode",
        "output",
        "config_only",
    ]
    assert by_name["output"].kind is Path and by_name["output"].optional
    assert by_name["mode"].kind is str and by_name["mode"].choices == ("fast", "slow")
    assert by_name["count"].label == "Count"
    assert by_name["quality"].limits() == "between 1 and 100"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"count": 0}, "count must be greater than 0, got 0"),
        ({"quality": 101}, "quality must be between 1 and 100, got 101"),
        ({"seconds": -1.5}, "seconds must be greater than 0, got -1.5"),
        ({"mode": "medium"}, "mode must be one of fast, slow"),
        ({"count": True}, "count must be int"),
        ({"count": None}, "count must be set"),
    ],
)
def test_validate_rejects_bad_values(values, message):
    with pytest.raises(ValueError, match=message):
        Task(**values)


def test_flags_default_to_none_and_only_given_values_are_read():
    parser = parser_for(Task)
    assert given_values(Task, parser.parse_args([])) == {}
    args = parser.parse_args(["-n", "4", "--no-precise", "--mode", "slow", "--output", "x"])
    assert given_values(Task, args) == {
        "count": 4,
        "precise": False,
        "mode": "slow",
        "output": Path("x"),
    }
    assert not hasattr(args, "config_only") and not hasattr(args, "internal")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--count", "0"], "must be greater than 0, got 0"),
        (["--count", "two"], "expected a whole number"),
        (["--quality", "101"], "must be between 1 and 100"),
        (["--seconds", "abc"], "expected a number"),
    ],
)
def test_flags_are_checked_by_argparse(argv, message):
    with pytest.raises(argparse.ArgumentError, match=message):
        parser_for(Task).parse_args(argv)


def test_help_shows_defaults_and_groups():
    text = parser_for(Task).format_help()
    assert "how many (default: 10)" in text
    assert "exact positions (default: off)" in text
    assert "where to write (default: next to the video)" in text
    assert "main:" in text


def test_shared_options_are_added_once():
    @dataclass
    class Other:
        quality: int = option(80, "JPEG quality", ge=1, le=100)

    parser = parser_for(Task, Other)  # would raise on a duplicated --quality
    assert "(default: 90)" in parser.format_help()


def test_parse_mapping_converts_and_checks():
    values = parse_mapping(Task, {"seconds": 1, "output": "~/out", "config_only": 5})
    assert values["seconds"] == 1.0 and isinstance(values["seconds"], float)
    assert values["output"] == Path("~/out").expanduser()
    assert values["config_only"] == 5


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"bogus": 1}, "unknown option 'bogus' \\(valid options: count, quality"),
        ({"count": "many"}, "'count' must be int, got 'many'"),
        ({"precise": 1}, "'precise' must be bool"),
        ({"quality": 0}, "'quality' must be between 1 and 100, got 0"),
        ({"output": 3}, "'output' must be a path or null"),
        ({"internal": 1}, "unknown option 'internal'"),
    ],
)
def test_parse_mapping_rejects_bad_values(data, message):
    with pytest.raises(ValueError, match=message):
        parse_mapping(Task, data)


def test_unsupported_types_are_reported():
    @dataclass
    class Bad:
        items: list = option(None, "a list")

    with pytest.raises(TypeError, match="Bad.items"):
        options_of(Bad)
