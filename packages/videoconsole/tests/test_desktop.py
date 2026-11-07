import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from videoconsole import Console, ConsoleOptions, Desktop, Wording, desktop, keep_awake
from videoconsole.desktop import launched_alone, output_folder
from videocore import ItemResult, RunReport, Status, plan
from videocore.outputs import OutputSlot


@dataclass(frozen=True)
class Options:
    pass


class Task:
    name = "fake"

    def output(self, slot: OutputSlot) -> Path:
        return slot.folder / f"{slot.name}.txt"

    def state(self, video, output, name, options):
        return None

    def estimate(self, footage, options):
        return footage.duration


class Kernel32:
    def __init__(self):
        self.states = []

    def SetThreadExecutionState(self, flags):  # noqa: N802 - the Windows name
        self.states.append(flags)
        return 1


@pytest.fixture
def planned(tmp_path):
    folder = tmp_path / "videos"
    folder.mkdir()
    video = folder / "a.mp4"
    video.write_bytes(b"x")
    return plan(Task(), [video], Options(), source=folder, measure=False)


@pytest.fixture
def calls(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "notify", lambda app, title, text: calls.append((title, text)))
    monkeypatch.setattr(desktop, "open_folder", lambda folder: calls.append(("open", folder)))
    return calls


def finished(planned, status=Status.OK):
    item = planned.items[0]
    return RunReport(planned, [ItemResult(item.video, item.output, status)])


def test_keep_awake_is_released_even_on_errors(monkeypatch):
    kernel32 = Kernel32()
    monkeypatch.setattr(desktop, "_kernel32", lambda: kernel32)
    with pytest.raises(RuntimeError), keep_awake():
        assert kernel32.states == [0x80000001]
        raise RuntimeError
    assert kernel32.states == [0x80000001, 0x80000000]

    with keep_awake(enabled=False):
        pass
    assert len(kernel32.states) == 2


@pytest.mark.parametrize(
    ("programs", "alone"),
    [
        (["videothumb", "python", "python"], True),
        (["pwsh", "uv", "python"], False),
        (["cmd", "python"], False),
        (None, False),  # unknown
    ],
)
def test_launched_alone_means_no_shell_on_the_console(monkeypatch, programs, alone):
    monkeypatch.setattr(desktop, "console_programs", lambda: programs)
    monkeypatch.setattr(desktop, "started_hidden", lambda: False)
    monkeypatch.setattr(desktop, "started_by_service", lambda: False)
    monkeypatch.setattr("sys.stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})())
    assert launched_alone() is alone


def test_a_hidden_window_is_never_alone(monkeypatch):
    # Start-Process -WindowStyle Hidden or a scheduled task: nobody could press Enter.
    monkeypatch.setattr(desktop, "console_programs", lambda: ["videothumb", "python"])
    monkeypatch.setattr(desktop, "started_hidden", lambda: True)
    monkeypatch.setattr("sys.stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})())
    assert launched_alone() is False


def test_a_scheduled_task_is_never_alone(monkeypatch):
    # The Task Scheduler shows the window of a task, but nobody waits in front of it.
    monkeypatch.setattr(desktop, "console_programs", lambda: ["videothumb", "python"])
    monkeypatch.setattr(desktop, "started_hidden", lambda: False)
    monkeypatch.setattr(desktop, "started_by_service", lambda: True)
    monkeypatch.setattr("sys.stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})())
    assert launched_alone() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process table")
def test_started_by_service_walks_up_to_the_starting_program(monkeypatch):
    real, me = desktop._processes, os.getpid()
    table = {me: (10, "python"), 10: (20, "vthumb"), 20: (4, "svchost")}
    monkeypatch.setattr(desktop, "_processes", lambda kernel32: table)
    monkeypatch.setattr(desktop, "_console_pids", lambda kernel32: [me, 10])
    assert desktop.started_by_service() is True
    table[20] = (4, "explorer")
    assert desktop.started_by_service() is False
    assert len(real(desktop._kernel32())) > 1  # the real table can be read


def test_started_hidden_reads_the_startup_info():
    assert desktop.started_hidden() is False  # pytest runs in a visible console, or none


def test_nobody_to_answer_is_never_alone(monkeypatch):
    monkeypatch.setattr(desktop, "console_programs", lambda: ["python"])
    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert launched_alone() is False


def test_long_runs_end_with_a_notification(planned, calls):
    session = Desktop("app", Console(), Wording(done="archived"))
    with session.running(planned):
        pass
    session.report = finished(planned)
    session.finish(0)
    assert calls == []  # it was quick

    session.seconds = 5 * 60
    session.report = finished(planned, Status.FAILED)
    session.finish(1)
    assert calls == [("app finished with failures", "0 archived, 1 failed")]

    calls.clear()
    session.configure(ConsoleOptions(notify_after=0))
    session.finish(1)
    assert calls == []


def test_open_output_only_when_something_was_made(planned, calls):
    session = Desktop("app", Console(), Wording(), open_output=True)
    with session.running(planned):
        pass
    session.report = finished(planned, Status.FAILED)
    session.finish(1)
    assert calls == []
    session.report = finished(planned)
    session.finish(0)
    assert calls == [("open", planned.source)]


def test_pause_when_alone_can_open_the_folder(planned, calls, monkeypatch, capsys):
    monkeypatch.setattr(desktop, "launched_alone", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("o\n"))
    session = Desktop("app", Console(), Wording())
    with session.running(planned):
        pass
    session.report = finished(planned)
    assert session.finish(3) == 3
    prompt = "Press Enter to close, or O and Enter to open the output folder"
    assert prompt in capsys.readouterr().err
    assert calls == [("open", planned.source)]

    session.configure(ConsoleOptions(pause=False))
    calls.clear()
    session.finish(0)
    assert calls == [] and capsys.readouterr().err == ""


def test_output_folder(planned, tmp_path):
    assert output_folder(planned) == planned.source
    single = plan(Task(), [planned.items[0].video], Options(), outputs=[tmp_path / "x.jpg"])
    assert output_folder(single) == tmp_path
    gone = plan(Task(), [planned.items[0].video], Options(), outputs=[tmp_path / "no" / "x.jpg"])
    assert output_folder(gone) is None
