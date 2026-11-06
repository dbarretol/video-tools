import functools
import multiprocessing
import threading
import time

import pytest

from videocore import Cancelled, CancelToken, batch, progress, run_parallel, sample_frames
from videocore.progress import ProgressEvent


def collect(item="clip.mp4", cancelled=None):
    events = []
    context = progress.reporting(item, events.append, cancelled)
    return events, context


def test_outside_a_run_reporting_does_nothing():
    progress.stage("frames", 1, 10)
    progress.check_cancelled()


def test_events_are_tagged_with_the_item_and_throttled():
    events, context = collect()
    with context:
        progress.stage("probe")
        for done in range(1, 101):
            progress.stage("frames", done, 100)

    assert events[0] == ProgressEvent("clip.mp4", "started")
    assert events[1] == ProgressEvent("clip.mp4", "stage", "probe")
    frames = [e for e in events if e.stage == "frames"]
    # The first step and the last one always get through; the rest are throttled.
    assert frames[0].done == 1 and frames[-1].done == 100 and frames[-1].fraction == 1.0
    assert len(frames) < 10


def test_reporting_restores_the_previous_context():
    outer, outer_context = collect("outer.mp4")
    inner, inner_context = collect("inner.mp4")
    with outer_context:
        with inner_context:
            progress.stage("a")
        progress.stage("b")
    assert [e.stage for e in inner] == [None, "a"]
    assert [e.stage for e in outer] == [None, "b"]


def test_check_cancelled_raises_once_requested():
    token = CancelToken()
    _, context = collect(cancelled=token)
    with context:
        progress.check_cancelled()
        token.cancel()
        assert token.cancelled
        with pytest.raises(Cancelled, match="clip.mp4"):
            progress.check_cancelled()
    # Cancelled is not an Exception, so `except Exception` in tasks lets it through.
    assert not issubclass(Cancelled, Exception)


def test_frames_report_their_stage_and_stop_when_cancelled(video_only):
    events, context = collect()
    with context:
        frames = sample_frames(video_only, [0.1, 0.2, 0.3], stage="grid")
    assert all(frame is not None for frame in frames)
    assert [e.done for e in events if e.stage == "grid"][-1] == 3

    _, context = collect(cancelled=lambda: True)
    with context, pytest.raises(Cancelled):
        sample_frames(video_only, [0.1, 0.2, 0.3], workers=2)


def test_inline_runs_report_events_and_stop_cooperatively():
    events, finished = [], []
    token = CancelToken()

    def work(item):
        for step in range(1, 4):
            progress.stage("steps", step, 3)
            if item == "b" and step == 2:
                token.cancel()
            progress.check_cancelled()
        return item

    results = run_parallel(
        work,
        ["a", "b", "c"],
        workers=1,
        on_event=events.append,
        on_progress=lambda done, total, r: finished.append(r.item),
        should_cancel=token,
    )

    assert [(r.item, r.ok, r.cancelled) for r in results] == [
        ("a", True, False),
        ("b", False, True),
    ]
    assert finished == ["a"]  # a cancelled item is not reported as finished
    assert [e.item for e in events if e.kind == "started"] == ["a", "b"]
    assert ProgressEvent("a", "stage", "steps", 3, 3) in events


def test_workers_send_events_and_stop_cooperatively(video_only, monkeypatch):
    # Precise sampling of many positions keeps each worker busy for a while.
    monkeypatch.setattr(batch, "CANCEL_GRACE_SECONDS", 20.0)
    timestamps = [i / 1000 for i in range(3000)]
    work = functools.partial(sample_frames, mode="precise", stage="frames")
    events = []
    started = threading.Event()

    def on_event(event):
        events.append(event)
        if event.stage == "frames" and event.done > 0:
            started.set()

    begin = time.monotonic()
    results = run_parallel(
        work,
        [video_only, video_only],
        item_args=[timestamps, timestamps],
        workers=2,
        on_event=on_event,
        should_cancel=started.is_set,
    )

    # Both stopped by themselves, well before the grace period would have them killed.
    assert [r.cancelled for r in results] == [True, True]
    assert time.monotonic() - begin < 15
    assert sum(e.kind == "started" for e in events) == 2
    assert multiprocessing.active_children() == []
