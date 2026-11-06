"""What a command line does for the desktop around a run (Windows; no-ops elsewhere).

- The computer does not go to sleep while videos are processed (`keep_awake`).
- A run that took long ends with a notification (`notify`), so it can be left
  in the background.
- Started from the Explorer (double click, a shortcut, a file dropped on it)
  the console window would close as soon as the run ends, before its report
  can be read; `launched_alone` detects it, and `Desktop.finish` then waits for
  Enter and offers to open the output folder.
- ``--open`` opens the output folder at the end (`open_folder`).
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videoconsole.console import Console
from videoconsole.run import Wording, summary_line
from videocore import Plan, RunReport, Status
from videocore.options import option

logger = logging.getLogger(__name__)

SHELLS = frozenset(
    {"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "nu", "tcc", "wsl", "far", "clink"}
)
"""Programs that, attached to our console, mean we were started from a terminal."""

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_CLASSIC_CONSOLE = "ConsoleWindowClass"
"""Window class of a conhost console; Windows Terminal uses ``PseudoConsoleWindow``."""
_TH32CS_SNAPPROCESS = 0x2
SERVICE_HOSTS = frozenset({"svchost", "taskeng", "taskhostw", "services", "wmiprvse"})
"""Programs that start others for nobody to watch: the Task Scheduler, services, WMI."""


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_uint32),
        ("dwY", ctypes.c_uint32),
        ("dwXSize", ctypes.c_uint32),
        ("dwYSize", ctypes.c_uint32),
        ("dwXCountChars", ctypes.c_uint32),
        ("dwYCountChars", ctypes.c_uint32),
        ("dwFillAttribute", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class ConsoleOptions:
    """How the command line behaves on the desktop; config file only (``console`` section)."""

    notify_after: float = option(
        5.0,
        "minutes a run must take to end with a desktop notification; 0 turns it off",
        ge=0,
        cli=False,
    )
    keep_awake: bool = option(
        True, "keep the computer from going to sleep while videos are processed", cli=False
    )
    pause: bool = option(
        True,
        "when started from the Explorer, wait for Enter before closing the window",
        cli=False,
    )


def _kernel32() -> Any | None:
    if sys.platform != "win32":
        return None
    return ctypes.windll.kernel32  # type: ignore[attr-defined]


@contextmanager
def keep_awake(enabled: bool = True) -> Iterator[None]:
    """Keep the computer from sleeping until the block ends (the screen may still turn off)."""
    kernel32 = _kernel32() if enabled else None
    if kernel32 is None:
        yield
        return
    kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        kernel32.SetThreadExecutionState(_ES_CONTINUOUS)


def console_programs() -> list[str] | None:
    """Names (lowercase, no extension) of the programs attached to this console.

    None when unknown: not on Windows, no console, or a program that cannot be
    inspected (e.g. an elevated shell).
    """
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    pids = _console_pids(kernel32)
    if pids is None:
        return None
    names = []
    for pid in pids:
        name = _image_name(kernel32, pid)
        if name is None:
            return None
        names.append(name)
    return names


def _console_pids(kernel32: Any) -> list[int] | None:
    pids = (ctypes.c_uint32 * 64)()
    count = kernel32.GetConsoleProcessList(pids, len(pids))
    if not 0 < count <= len(pids):
        return None
    return list(pids[:count])


def _processes(kernel32: Any) -> dict[int, tuple[int, str]]:
    """Every running process: pid -> (parent pid, lowercase name without extension)."""
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot is None or snapshot == ctypes.c_void_p(-1).value:
        return {}
    handle = ctypes.c_void_p(snapshot)
    table = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            name = Path(entry.szExeFile).stem.lower()
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, name)
            more = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return table


def started_by_service() -> bool:
    """Whether the program that started us is the Task Scheduler or another service.

    Its tasks may show a console window, but nobody is there to press Enter. The
    programs of this run that share our console (the launcher of the ``.exe``,
    Python) are skipped to reach the one that started them.
    """
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    ours = set(_console_pids(kernel32) or [])
    table = _processes(kernel32)
    pid, seen = os.getpid(), set()
    while pid in ours and pid in table and pid not in seen:
        seen.add(pid)
        pid = table[pid][0]
    parent = table.get(pid)
    return parent is not None and parent[1] in SERVICE_HOSTS


def _image_name(kernel32: Any, pid: int) -> str | None:
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_uint32(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).stem.lower()
    finally:
        kernel32.CloseHandle(handle)


def started_hidden() -> bool:
    """Whether this process was started with its window hidden.

    Scripts (``Start-Process -WindowStyle Hidden``) and scheduled tasks do that:
    the console is ours alone, but nobody can see it to press Enter. A hidden
    console is a classic console window that is not visible. The start-up
    request is checked too, although launchers such as the venv's
    ``python.exe`` pass the value on without the flag that makes it count.
    A pseudo console (Windows Terminal) belongs to a terminal someone sees.
    """
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, len(name))
        if name.value == _CLASSIC_CONSOLE and not user32.IsWindowVisible(hwnd):
            return True
    info = _StartupInfo()
    info.cb = ctypes.sizeof(info)
    kernel32.GetStartupInfoW(ctypes.byref(info))
    return bool(info.dwFlags & _STARTF_USESHOWWINDOW) and info.wShowWindow == _SW_HIDE


def launched_alone() -> bool:
    """Whether this console window was opened for us, so it closes when we exit.

    True when no shell is attached to the console (started from the Explorer
    or a shortcut), someone can answer (stdin is a terminal), the window is
    not hidden (see `started_hidden`) and no service such as the Task Scheduler
    started us (see `started_by_service`).
    """
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    programs = console_programs()
    if programs is None or SHELLS.intersection(programs):
        return False
    return not started_hidden() and not started_by_service()


def open_folder(folder: Path) -> None:
    """Open `folder` in the file manager; errors are logged, not raised."""
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(folder)])  # noqa: S603
    except OSError:
        logger.warning("Could not open %s", folder, exc_info=True)


def notify(app: str, title: str, message: str) -> bool:
    """Show a desktop notification; False when none could be shown.

    Uses the optional ``windows-toasts`` package (``videoconsole[notify]``);
    without it, the console window flashes in the taskbar instead.
    """
    try:
        from windows_toasts import Toast, WindowsToaster
    except ImportError:
        return _flash_console()
    try:
        WindowsToaster(app).show_toast(Toast([title, message]))
    except Exception:  # noqa: BLE001 - a notification is never worth failing a run
        logger.debug("Could not show a notification", exc_info=True)
        return _flash_console()
    return True


class _FlashInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


def _flash_console() -> bool:
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        return False
    flash_all, until_foreground = 0x3, 0xC
    info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, flash_all | until_foreground, 0, 0)
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))  # type: ignore[attr-defined]
    return True


def output_folder(planned: Plan[Any]) -> Path | None:
    """The folder to open after a run: the output folder, the scanned one, or the single output."""
    if planned.run.output is not None:
        folder = planned.run.output
    elif planned.source is not None:
        folder = planned.source
    elif planned.items:
        output = planned.items[0].output
        folder = output if output.is_dir() else output.parent
    else:
        return None
    return folder if folder.is_dir() else None


def pause(console: Console, folder: Path | None) -> None:
    """Wait for Enter; with `folder`, typing O first opens it."""
    prompt = "Press Enter to close"
    if folder is not None:
        prompt += ", or O and Enter to open the output folder"
    console.err.print(prompt + ": ", end="")
    try:
        answer = sys.stdin.readline()
    except (OSError, KeyboardInterrupt):
        return
    if folder is not None and answer.strip().lower() == "o":
        open_folder(folder)


class Desktop:
    """The desktop side of one command-line run.

    Create it right after parsing; `configure` with the ``console`` section of
    the config once it is read; run the work inside `running`, telling it the
    plan and report; end with `finish`.
    """

    def __init__(
        self, app: str, console: Console, wording: Wording, *, open_output: bool = False
    ) -> None:
        self.app = app
        self.console = console
        self.wording = wording
        self.open_output = open_output
        self.options = ConsoleOptions()
        self.alone = launched_alone()
        self.plan: Plan[Any] | None = None
        self.report: RunReport | None = None
        self.seconds = 0.0

    def configure(self, options: ConsoleOptions) -> None:
        self.options = options

    @contextmanager
    def running(self, planned: Plan[Any]) -> Iterator[None]:
        """Around ``execute``: keeps the computer awake and measures the run.

        Set `report` to what ``execute`` returned.
        """
        self.plan = planned
        start = time.monotonic()
        try:
            with keep_awake(self.options.keep_awake):
                yield
        finally:
            self.seconds += time.monotonic() - start

    def finish(self, exit_code: int) -> int:
        """Notify after a long run, open the output with ``--open``, pause if launched alone."""
        minutes = self.options.notify_after
        if self.report is not None and minutes > 0 and self.seconds >= minutes * 60:
            notify(self.app, self._title(), summary_line(self.report, self.wording))
        folder = output_folder(self.plan) if self.plan is not None else None
        if self.open_output and folder is not None and self._produced():
            open_folder(folder)
            folder = None  # already open
        if self.alone and self.options.pause:
            pause(self.console, folder)
        return exit_code

    def _produced(self) -> bool:
        if self.report is None:
            return self.plan is not None and not self.plan.pending  # everything already done
        return any(item.ok for item in self.report.items)

    def _title(self) -> str:
        report = self.report
        if report is None:
            return f"{self.app} finished"
        if report.interrupted:
            return f"{self.app} was interrupted"
        if report.failed:
            return f"{self.app} finished with failures"
        if report.degraded or any(i.status is Status.CANCELLED for i in report.items):
            return f"{self.app} finished with warnings"
        return f"{self.app} finished"


def pause_if_alone() -> None:
    """Wait for Enter when launched from the Explorer, e.g. before exiting on invalid arguments."""
    if launched_alone():
        pause(Console(), None)
