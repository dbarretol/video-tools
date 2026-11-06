import io
import logging
from dataclasses import dataclass
from pathlib import Path

import pytest
from rich.console import Console as RichConsole

from videoconsole import (
    Console,
    RunProgress,
    Wording,
    print_plan,
    print_report,
    report_run_lists,
    setup_logging,
)
from videocore import ItemResult, ProgressEvent, RunReport, Status, plan
from videocore.outputs import OutputSlot

WORDING = Wording(verb="Archiving", done="archived", stages={"thumb": "thumbnail grid"})


@dataclass(frozen=True)
class Options:
    pass


class Task:
    name = "fake"

    def output(self, slot: OutputSlot) -> Path:
        return slot.folder / f"{slot.name}.txt"

    def state(self, output, name, options):
        return Status.SKIPPED if output.exists() else None

    def estimate(self, duration, options):
        return duration


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "localappdata"))


@pytest.fixture
def planned(tmp_path):
    folder = tmp_path / "videos"
    (folder / "sub").mkdir(parents=True)
    videos = [folder / "a.mp4", folder / "sub" / "b.mp4", folder / "c.mp4"]
    for video in videos:
        video.write_bytes(b"x")
    (folder / "c.txt").write_text("done")
    return plan(Task(), videos, Options(), source=folder, measure=False)


def results(planned, *statuses, **fields):
    return [
        ItemResult(item.video, item.output, status, **fields)
        for item, status in zip(planned.items, statuses, strict=False)
    ]


def terminal(console: Console) -> io.StringIO:
    """Make stderr of `console` a terminal, so live progress is drawn into the returned buffer."""
    buffer = io.StringIO()
    console.err = RichConsole(file=buffer, force_terminal=True, width=120, markup=False)
    return buffer


def test_plan_and_items(planned, capsys):
    print_plan(Console(), planned, WORDING, items=True)
    out = capsys.readouterr().out
    assert out.startswith("3 videos: 2 to process, 1 already done\n")
    assert f"[process] {Path('sub/b.mp4')} -> {Path('sub/b.txt')}" in out


def test_without_a_terminal_each_video_prints_a_line(planned, capsys):
    a, b = results(planned, Status.OK, Status.FAILED)
    with RunProgress(Console(), planned, WORDING) as progress:
        progress.on_event(ProgressEvent(a.video, "started"))
        progress.on_event(ProgressEvent(a.video, "stage", "thumb", 1, 4))
        progress.on_progress(1, 2, a)
        progress.on_progress(2, 2, b)
    assert capsys.readouterr().out == f"[1/2] a.mp4: OK\n[2/2] {Path('sub/b.mp4')}: FAILED\n"


def test_stages_are_logged_once_per_video(planned, caplog):
    caplog.set_level(logging.DEBUG)
    video = planned.items[0].video
    with RunProgress(Console(), planned, WORDING) as progress:
        for done in range(3):
            progress.on_event(ProgressEvent(video, "stage", "thumb", done, 3))
    assert [r.getMessage() for r in caplog.records] == ["a.mp4: thumbnail grid"]


def test_in_a_terminal_progress_is_drawn_and_results_stay(planned, capsys):
    console = Console()
    buffer = terminal(console)
    a, b = results(planned, Status.OK, Status.DEGRADED)
    with RunProgress(console, planned, WORDING) as progress:
        progress.on_event(ProgressEvent(a.video, "started"))
        progress.on_event(ProgressEvent(a.video, "stage", "thumb", 2, 4))
        progress._progress.refresh()
        drawn = buffer.getvalue()
        assert "Archiving 0/2 videos" in drawn
        assert "a.mp4: thumbnail grid" in drawn
        progress.on_progress(1, 2, a)
        progress.on_progress(2, 2, b)
    text = buffer.getvalue()
    assert "[1/2] a.mp4: " in text and "DEGRADED" in text
    assert capsys.readouterr().out == ""  # results go above the bars, on stderr


def test_quiet_draws_nothing(planned, capsys):
    console = Console(quiet=True)
    buffer = terminal(console)
    (a,) = results(planned, Status.OK)
    with RunProgress(console, planned, WORDING) as progress:
        progress.on_event(ProgressEvent(a.video, "started"))
        progress.on_progress(1, 2, a)
    assert buffer.getvalue() == "" and capsys.readouterr() == ("", "")


def test_report(planned, tmp_path, capsys):
    items = results(planned, Status.FAILED, Status.DEGRADED, Status.SKIPPED)
    items[0].error = "VideoOpenError: Could not open video"
    items[0].hint = "the file may be damaged"
    items[1].problems = ["thumbnail grid: only 1/4 frames"]
    report = RunReport(planned, items)
    print_report(Console(), report, WORDING, log_file=tmp_path / "app.log")
    captured = capsys.readouterr()
    summary = "Done: 0 archived, 1 incomplete, 1 skipped (output already exists), 1 failed."
    assert captured.out == summary + "\n"
    assert captured.err.splitlines() == [
        f"  - {Path('sub/b.mp4')}: incomplete: thumbnail grid: only 1/4 frames",
        "  - a.mp4: VideoOpenError: Could not open video",
        "    Hint: the file may be damaged",
        f"Details: {tmp_path / 'app.log'}",
    ]


def test_quiet_report_keeps_the_problems(planned, capsys):
    items = results(planned, Status.OK, Status.CANCELLED, Status.SKIPPED)
    report = RunReport(planned, items, interrupted=True)
    console = Console(quiet=True)
    print_report(console, report, WORDING, log_file=None)
    lists = report_run_lists(console, report, "app", title="app run", resume="run it again")
    captured = capsys.readouterr()
    assert captured.err == "Interrupted; unfinished files were removed.\n"
    resume = f"Unfinished videos are listed in {lists.pending}; to resume, run it again"
    assert captured.out == resume + "\n"


def test_text_is_printed_literally(capsys):
    Console().info("clip [bold]1080p[/bold] :smile:.mp4")
    assert capsys.readouterr().out == "clip [bold]1080p[/bold] :smile:.mp4\n"


def test_long_lines_are_not_wrapped(capsys):
    line = "x" * 300
    Console().info(line)
    assert capsys.readouterr().out == line + "\n"


def test_debug_messages_reach_the_console_only_when_verbose(capsys):
    log = logging.getLogger("videoconsole.test")
    log_file = setup_logging("app", Console(), verbose=False)
    log.info("to the file only")
    assert capsys.readouterr().err == ""

    setup_logging("app", Console(), verbose=True)
    log.debug("shown")
    assert "shown" in capsys.readouterr().err
    assert "to the file only" in log_file.read_text(encoding="utf-8")
    setup_logging("app", Console(), verbose=False)  # leave no console handler behind


def test_json_document(planned, tmp_path, capsys):
    import json

    console = Console(json=True, tool="app")
    print_plan(console, planned, WORDING)
    console.info("for people")
    assert console.finish(3, log_file=tmp_path / "ñ.log") == 3
    captured = capsys.readouterr()
    assert captured.out.isascii() and "for people" in captured.err
    document = json.loads(captured.out)
    assert document["tool"] == "app" and document["exit_code"] == 3
    assert document["log_file"] == str(tmp_path / "ñ.log")
    assert document["plan"]["counts"] == {"pending": 2, "skipped": 1, "outdated": 0}

    plain = Console()
    plain.record(anything=1)
    assert plain.finish(0) == 0 and capsys.readouterr().out == ""


def test_no_progress_turns_off_the_live_display():
    assert Console(progress=False).live is False
