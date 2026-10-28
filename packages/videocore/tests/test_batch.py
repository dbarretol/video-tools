import logging
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from videocore import find_videos, run_parallel


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


def test_cancelling_stops_running_workers_promptly():
    # time.sleep is a picklable builtin: each item keeps a worker busy for 30 s.
    start = time.monotonic()
    results = run_parallel(time.sleep, [30, 30, 30], workers=2, should_cancel=_cancel_after(1))
    assert results == []
    assert time.monotonic() - start < 15
    # Workers are gone, not just abandoned: nothing would keep the interpreter from exiting.
    assert multiprocessing.active_children() == []


def test_interruption_stops_running_workers():
    def interrupt():
        if time.monotonic() - start > 1:
            raise RuntimeError("interrupted")  # stands in for KeyboardInterrupt
        return False

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="interrupted"):
        run_parallel(time.sleep, [30, 30], workers=2, should_cancel=interrupt)
    assert time.monotonic() - start < 15
    assert multiprocessing.active_children() == []
