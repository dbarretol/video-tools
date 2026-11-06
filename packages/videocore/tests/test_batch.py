import ctypes
import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

from videocore import batch, find_videos, run_parallel


def test_find_videos_filters_and_sorts(tmp_path):
    for name in ["b.MP4", "a.mkv", "notes.txt", "c.webm"]:
        (tmp_path / name).touch()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.mov").touch()

    assert [p.name for p in find_videos(tmp_path)] == ["a.mkv", "b.MP4", "c.webm"]
    assert [p.name for p in find_videos(tmp_path, recursive=True)] == [
        "a.mkv",
        "b.MP4",
        "c.webm",
        "d.mov",
    ]


def test_find_videos_ignores_hidden_files_and_folders(tmp_path):
    for name in ["clip.mp4", "._clip.mp4", ".hidden.mkv"]:
        (tmp_path / name).touch()
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "inner.mp4").touch()

    assert [p.name for p in find_videos(tmp_path, recursive=True)] == ["clip.mp4"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes")
def test_find_videos_ignores_windows_hidden_attribute(tmp_path):
    (tmp_path / "shown.mp4").touch()
    hidden = tmp_path / "hidden.mp4"
    hidden.touch()
    hidden_folder = tmp_path / "Recycled"
    hidden_folder.mkdir()
    (hidden_folder / "deleted.mp4").touch()
    for path in (hidden, hidden_folder):
        assert ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN

    assert [p.name for p in find_videos(tmp_path, recursive=True)] == ["shown.mp4"]


def test_find_videos_skip_dir_prunes_subfolders(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.mp4").touch()
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "b.mp4").touch()
    (tmp_path / "skip" / "deeper").mkdir()
    (tmp_path / "skip" / "deeper" / "c.mp4").touch()
    (tmp_path / "top.mp4").touch()
    seen = []

    def skip(path):
        seen.append(path.name)
        return path.name == "skip"

    found = find_videos(tmp_path, recursive=True, skip_dir=skip)

    assert [p.name for p in found] == ["a.mp4", "top.mp4"]
    assert "deeper" not in seen  # never entered the skipped folder


def test_find_videos_skip_dir_does_not_hide_top_level_files(tmp_path):
    (tmp_path / "top.mp4").touch()
    assert find_videos(tmp_path, skip_dir=lambda path: True) == [tmp_path / "top.mp4"]


def test_find_videos_missing_folder(tmp_path):
    with pytest.raises(NotADirectoryError):
        find_videos(tmp_path / "missing")


@pytest.mark.parametrize("workers", [1, 2])
def test_run_parallel_isolates_errors_and_keeps_order(tmp_path, workers):
    # os.path.getsize is importable in worker processes, unlike functions defined in tests.
    (tmp_path / "one.mp4").write_bytes(b"123")
    (tmp_path / "three.mp4").write_bytes(b"12345")
    items = [tmp_path / "one.mp4", tmp_path / "missing.mp4", tmp_path / "three.mp4"]
    progress = []

    results = run_parallel(
        os.path.getsize, items, workers=workers, on_progress=lambda d, t, r: progress.append(d)
    )

    assert [r.item for r in results] == items
    assert [r.ok for r in results] == [True, False, True]
    assert [r.value for r in results] == [3, None, 5]
    assert results[1].error.startswith("FileNotFoundError")
    assert sorted(progress) == [1, 2, 3]


@pytest.mark.parametrize("workers", [1, 2])
def test_run_parallel_passes_item_args(workers):
    items = [Path("a"), Path("b")]

    results = run_parallel(os.path.join, items, item_args=["x", "y"], workers=workers)

    assert [r.value for r in results] == [os.path.join("a", "x"), os.path.join("b", "y")]
    assert [r.item for r in results] == items


def test_run_parallel_item_args_must_match_items():
    with pytest.raises(ValueError, match="one value per item"):
        run_parallel(os.path.join, [Path("a")], item_args=[], workers=1)


def test_run_parallel_inline_cancellation():
    items = [Path(f"v{i}.mp4") for i in range(5)]
    seen = []

    results = run_parallel(
        str,
        items,
        workers=1,
        on_progress=lambda d, t, r: seen.append(d),
        should_cancel=lambda: len(seen) >= 2,
    )

    assert [r.item for r in results] == items[:2]


def test_worker_logs_reach_the_parent_handlers(tmp_path, caplog):
    items = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    # logging.warning is a picklable module-level function that logs its argument.
    with caplog.at_level(logging.WARNING):
        results = run_parallel(logging.warning, items, workers=2)
    assert all(r.ok for r in results)
    messages = {r.getMessage() for r in caplog.records}
    assert {str(item) for item in items} <= messages


def _cancel_after(seconds):
    start = time.monotonic()
    return lambda: time.monotonic() - start > seconds


def test_cancelling_stops_running_workers_promptly(monkeypatch):
    # time.sleep is a picklable builtin that never checks for cancellation, so the
    # workers are killed once the grace period is over.
    monkeypatch.setattr(batch, "CANCEL_GRACE_SECONDS", 0.5)
    start = time.monotonic()
    results = run_parallel(time.sleep, [30, 30, 30], workers=2, should_cancel=_cancel_after(1))
    assert results == []
    assert time.monotonic() - start < 15
    # Workers are gone, not just abandoned: nothing would keep the interpreter from exiting.
    assert multiprocessing.active_children() == []


def test_interruption_stops_running_workers(monkeypatch):
    monkeypatch.setattr(batch, "CANCEL_GRACE_SECONDS", 0.5)

    def interrupt():
        if time.monotonic() - start > 1:
            raise RuntimeError("interrupted")  # stands in for KeyboardInterrupt
        return False

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="interrupted"):
        run_parallel(time.sleep, [30, 30], workers=2, should_cancel=interrupt)
    assert time.monotonic() - start < 15
    assert multiprocessing.active_children() == []
