import json
import tkinter as tk

import pytest

from videothumb.common import Mode
from videothumb.gui.settings import (
    AppSettings,
    default_settings_path,
    load_settings,
    save_settings,
)
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings(mode="snapshots", snapshot_count=25, precise=True)
    save_settings(settings, path)
    assert load_settings(path) == settings


def test_missing_or_corrupt_settings_fall_back_to_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.json") == AppSettings()
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert load_settings(corrupt) == AppSettings()


def test_unknown_keys_and_wrong_types_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"columns": 7, "rows": "four", "theme": "dark"}), encoding="utf-8")
    assert load_settings(path) == AppSettings(columns=7)


def test_default_settings_path_is_in_local_app_data(tmp_path):
    # LOCALAPPDATA / XDG_STATE_HOME point into tmp_path (see conftest).
    assert default_settings_path().name == "settings.json"
    assert tmp_path in default_settings_path().parents


def test_options_from_settings():
    from videothumb.gui.app import options_from

    sheet = options_from(AppSettings(mode="sheet", columns=3, precise=True))
    assert sheet == SheetOptions(columns=3, sampling="precise")
    snapshots = options_from(AppSettings(mode="snapshots", snapshot_count=7))
    assert snapshots == SnapshotOptions(count=7)
    with pytest.raises(ValueError):
        options_from(AppSettings(columns=0))


def test_app_runs_a_single_video(clip, tmp_path):
    from videothumb.gui.app import ProgressScreen, ResultScreen, VideoThumbApp

    try:
        app = VideoThumbApp()
    except tk.TclError as exc:  # no display available
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        app.withdraw()
        video = tmp_path / "movie.mp4"
        video.write_bytes(clip.read_bytes())
        app.input_path, app.videos = video, [video]

        progress = app.screens[ProgressScreen]
        progress.start(Mode.SHEET, SheetOptions(columns=2, rows=1))
        for _ in range(200):
            app.update()
            if app.last_result is not None:
                break
            app.after(20)
        assert app.last_result is not None
        assert app.last_result.succeeded == 1
        assert (tmp_path / "movie_sheet.jpg").is_file()
        assert str(app.screens[ResultScreen]._title.cget("text")) == "Done"
    finally:
        app.destroy()
