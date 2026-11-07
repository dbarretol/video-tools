import json

import pytest

from videocore import State
from videocore.state import default_state_path


def test_state_lives_next_to_the_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_state_path("demo") == tmp_path / "demo" / "state.json"


def test_values_are_saved_and_read_back(tmp_path):
    path = tmp_path / "sub" / "state.json"
    state = State("demo", path)
    assert state.get("recent") is None
    state.set("recent", ["a", "b"])
    state.save()
    assert State("demo", path).get("recent") == ["a", "b"]


@pytest.mark.parametrize("text", ["{broken", "[1, 2]", ""])
def test_damaged_files_read_as_empty(tmp_path, text):
    path = tmp_path / "state.json"
    path.write_text(text, encoding="utf-8")
    assert State("demo", path).data == {}


def test_write_failures_are_ignored(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    state = State("demo", blocker / "state.json")  # its folder cannot be created
    state.set("x", 1)
    state.save()


def test_speed_is_learned_and_blended(tmp_path):
    path = tmp_path / "state.json"
    state = State("demo", path)
    assert state.speed("sheet") == 1.0

    state.learn_speed("sheet", None)
    assert not path.exists()  # nothing measured, nothing saved

    state.learn_speed("sheet", 3.0)  # the first measure is taken as is
    assert State("demo", path).speed("sheet") == 3.0
    state.learn_speed("sheet", 1.0)
    assert State("demo", path).speed("sheet") == 2.0
    assert json.loads(path.read_text(encoding="utf-8")) == {"speed": {"sheet": 2.0}}


def test_odd_speeds_are_ignored_or_limited(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"speed": {"a": "fast", "b": true, "c": 1000, "d": 0.5}}', encoding="utf-8")
    state = State("demo", path)
    assert [state.speed(task) for task in "abcd"] == [1.0, 1.0, 20.0, 0.5]
