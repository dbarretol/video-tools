"""Desktop app (tkinter): Home -> Settings -> Progress -> Result.

Work runs in a background thread using the same `jobs` module as the CLI, so
the window stays responsive; the thread reports to the UI through a queue that
is polled with `after()`.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from videocore import BatchResult, configure_logging, find_videos
from videocore.batch import VIDEO_EXTENSIONS
from videothumb.common import APP_NAME, Mode
from videothumb.gui.settings import AppSettings, load_settings, save_settings
from videothumb.gui.widgets import COLOR_ERROR, COLOR_SUCCESS, COLOR_WARNING, LabeledSpinbox
from videothumb.jobs import Options, process, process_folder
from videothumb.sheet import SheetOptions
from videothumb.snapshots import SnapshotOptions

logger = logging.getLogger(__name__)

POLL_MS = 100
# Upper bound to wait for batch workers to be stopped when the window is closed.
STOP_TIMEOUT_SECONDS = 10


@dataclass
class RunResult:
    succeeded: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    output_dir: Path | None = None
    cancelled: bool = False


class VideoThumbApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VideoThumb")
        self.geometry("520x380")
        self.minsize(460, 340)

        self.settings = load_settings()
        self.input_path: Path | None = None
        self.videos: list[Path] = []
        self.last_result: RunResult | None = None

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self.screens: dict[type, ttk.Frame] = {}
        for screen_cls in (HomeScreen, SettingsScreen, ProgressScreen, ResultScreen):
            screen = screen_cls(container, self)
            screen.grid(row=0, column=0, sticky="nsew")
            self.screens[screen_cls] = screen
        self.show(HomeScreen)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def close(self) -> None:
        """Close the window; stop running work first so nothing keeps running unseen."""
        progress = self.screens[ProgressScreen]
        assert isinstance(progress, ProgressScreen)
        if progress.busy:
            if not messagebox.askyesno(
                "Processing in progress",
                "Stop processing and close?\n\n"
                "Videos being processed are stopped and their output may be incomplete.",
                parent=self,
            ):
                return
            progress.stop(wait=True)
        self.destroy()

    def show(self, screen_cls: type) -> None:
        screen = self.screens[screen_cls]
        if hasattr(screen, "on_show"):
            screen.on_show()
        screen.tkraise()

    @property
    def is_batch(self) -> bool:
        return self.input_path is not None and self.input_path.is_dir()


class HomeScreen(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: VideoThumbApp) -> None:
        super().__init__(parent, padding=24)
        self.app = app
        ttk.Label(self, text="VideoThumb", font=("Segoe UI", 16, "bold")).pack(pady=(20, 30))
        ttk.Button(self, text="Select a video", command=self._select_video, width=28).pack(pady=6)
        ttk.Button(self, text="Select a folder", command=self._select_folder, width=28).pack(pady=6)
        self._hint = ttk.Label(self, text="", foreground=COLOR_ERROR, wraplength=440)
        self._hint.pack(pady=(16, 0))

    def _select_video(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
        path = filedialog.askopenfilename(
            title="Select a video", filetypes=[("Videos", patterns), ("All files", "*.*")]
        )
        if path:
            self.app.input_path, self.app.videos = Path(path), [Path(path)]
            self._hint.configure(text="")
            self.app.show(SettingsScreen)

    def _select_folder(self) -> None:
        path = filedialog.askdirectory(title="Select a folder with videos")
        if not path:
            return
        videos = find_videos(path)
        if not videos:
            self._hint.configure(text="No supported videos in that folder.")
            return
        self.app.input_path, self.app.videos = Path(path), videos
        self._hint.configure(text="")
        self.app.show(SettingsScreen)


class SettingsScreen(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: VideoThumbApp) -> None:
        super().__init__(parent, padding=16)
        self.app = app
        s = app.settings

        self._summary = ttk.Label(self, text="", font=("Segoe UI", 10, "bold"), wraplength=480)
        self._summary.pack(anchor="w")

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, pady=12)
        sheet_tab = ttk.Frame(self._notebook, padding=12)
        snapshots_tab = ttk.Frame(self._notebook, padding=12)
        self._notebook.add(sheet_tab, text="Contact sheet")
        self._notebook.add(snapshots_tab, text="Snapshots")
        if s.mode == Mode.SNAPSHOTS:
            self._notebook.select(snapshots_tab)

        self._columns = LabeledSpinbox(sheet_tab, "Columns", to=20, initial=s.columns)
        self._rows = LabeledSpinbox(sheet_tab, "Rows", to=20, initial=s.rows)
        self._width = LabeledSpinbox(
            sheet_tab, "Thumbnail width (px)", from_=80, to=1920, initial=s.thumbnail_width
        )
        self._count = LabeledSpinbox(
            snapshots_tab, "Number of snapshots", to=999, initial=s.snapshot_count
        )
        for widget in (self._columns, self._rows, self._width, self._count):
            widget.pack(anchor="w", pady=4)

        self._quality = LabeledSpinbox(self, "JPEG quality", to=100, initial=s.jpeg_quality)
        self._quality.pack(anchor="w")
        self._precise = tk.BooleanVar(value=s.precise)
        ttk.Checkbutton(self, text="Exact frame positions (slower)", variable=self._precise).pack(
            anchor="w", pady=(4, 8)
        )

        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Back", command=lambda: app.show(HomeScreen)).pack(side="left")
        ttk.Button(buttons, text="Generate", command=self._generate).pack(side="right")

    def on_show(self) -> None:
        if self.app.is_batch:
            text = f"Folder: {self.app.input_path}  ({len(self.app.videos)} videos)"
        else:
            text = f"Video: {self.app.input_path.name if self.app.input_path else ''}"
        self._summary.configure(text=text)

    def _generate(self) -> None:
        mode = Mode.SHEET if self._notebook.index("current") == 0 else Mode.SNAPSHOTS
        settings = AppSettings(
            mode=mode.value,
            columns=self._columns.get(),
            rows=self._rows.get(),
            thumbnail_width=self._width.get(),
            snapshot_count=self._count.get(),
            jpeg_quality=self._quality.get(),
            precise=self._precise.get(),
        )
        try:
            options = options_from(settings)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return
        self.app.settings = settings
        save_settings(settings)
        progress = self.app.screens[ProgressScreen]
        assert isinstance(progress, ProgressScreen)
        progress.start(mode, options)
        self.app.show(ProgressScreen)


def options_from(settings: AppSettings) -> Options:
    """Options for the selected mode; raises ValueError for invalid values."""
    sampling = "precise" if settings.precise else "keyframe"
    if settings.mode == Mode.SHEET:
        return SheetOptions(
            columns=settings.columns,
            rows=settings.rows,
            thumbnail_width=settings.thumbnail_width,
            jpeg_quality=settings.jpeg_quality,
            sampling=sampling,
        )
    return SnapshotOptions(
        count=settings.snapshot_count, jpeg_quality=settings.jpeg_quality, sampling=sampling
    )


class ProgressScreen(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: VideoThumbApp) -> None:
        super().__init__(parent, padding=24)
        self.app = app
        self._status = ttk.Label(self, text="", wraplength=460)
        self._status.pack(pady=(40, 12))
        self._bar = ttk.Progressbar(self, length=380)
        self._bar.pack(pady=8)
        self._cancel_button = ttk.Button(self, text="Cancel", command=self._cancel)
        self._cancel_button.pack(pady=16)
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self, mode: Mode, options: Options) -> None:
        self._cancel_event = threading.Event()
        if self.app.is_batch:
            self._bar.configure(mode="determinate", maximum=len(self.app.videos), value=0)
            self._status.configure(text=f"Processing {len(self.app.videos)} videos...")
            self._cancel_button.state(["!disabled"])
            self._cancel_button.pack(pady=16)
        else:
            # A single video is processed in this process and cannot be interrupted midway.
            self._bar.configure(mode="indeterminate")
            self._bar.start(15)
            self._status.configure(text=f"Processing {self.app.videos[0].name}...")
            self._cancel_button.pack_forget()
        # Daemon thread: closing the app must not wait for it (batch workers are stopped
        # explicitly in `stop`).
        self._worker = threading.Thread(target=self._work, args=(mode, options), daemon=True)
        self._worker.start()
        self.after(POLL_MS, self._poll)

    def stop(self, *, wait: bool = False) -> None:
        """Stop a running batch, killing workers that are mid-video."""
        self._cancel_event.set()
        if wait and self._worker is not None and self.app.is_batch:
            self._worker.join(timeout=STOP_TIMEOUT_SECONDS)

    def _cancel(self) -> None:
        self.stop()
        self._cancel_button.state(["disabled"])
        self._status.configure(text="Stopping...")

    def _work(self, mode: Mode, options: Options) -> None:
        app = self.app
        result = RunResult()
        try:
            if app.is_batch:
                results = process_folder(
                    app.videos,
                    mode,
                    options,
                    on_progress=lambda done, total, r: self._queue.put(("progress", done, r)),
                    should_cancel=self._cancel_event.is_set,
                )
                result.succeeded = sum(r.ok for r in results)
                result.failures = [(r.item.name, r.error or "") for r in results if not r.ok]
                result.output_dir = app.input_path
                result.cancelled = len(results) < len(app.videos)
            else:
                output = process(app.videos[0], mode, options)
                result.succeeded = 1
                result.output_dir = output if output.is_dir() else output.parent
        except Exception as exc:  # noqa: BLE001 - shown to the user, details in the log
            logger.exception("Processing failed")
            result.failures.append((app.videos[0].name if app.videos else "", str(exc)))
        self._queue.put(("done", result))

    def _poll(self) -> None:
        try:
            while True:
                message = self._queue.get_nowait()
                if message[0] == "progress":
                    _, done, item = message
                    assert isinstance(item, BatchResult)
                    self._bar.configure(value=done)
                    total = len(self.app.videos)
                    self._status.configure(text=f"[{done}/{total}] {item.item.name} finished")
                elif message[0] == "done":
                    self._bar.stop()
                    self.app.last_result = message[1]
                    self.app.show(ResultScreen)
                    return
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)


class ResultScreen(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: VideoThumbApp) -> None:
        super().__init__(parent, padding=24)
        self.app = app
        self._title = ttk.Label(self, font=("Segoe UI", 16, "bold"))
        self._title.pack(pady=(16, 4))
        self._summary = ttk.Label(self, wraplength=460, justify="left")
        self._summary.pack(pady=4)
        self._failures = tk.Listbox(self, height=6)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", side="bottom", pady=(16, 0))
        self._open_button = ttk.Button(buttons, text="Open output folder", command=self._open)
        self._open_button.pack(side="left")
        ttk.Button(buttons, text="Start over", command=self._restart).pack(side="right")

    def on_show(self) -> None:
        result = self.app.last_result or RunResult()
        total = result.succeeded + len(result.failures)
        if result.failures and not result.succeeded:
            self._title.configure(text="Failed", foreground=COLOR_ERROR)
        elif result.failures or result.cancelled:
            self._title.configure(text="Finished with problems", foreground=COLOR_WARNING)
        else:
            self._title.configure(text="Done", foreground=COLOR_SUCCESS)

        summary = f"{result.succeeded} of {total} videos processed."
        if result.cancelled:
            summary += " Cancelled: unfinished videos were stopped and may have incomplete output."
        self._summary.configure(text=summary)

        self._failures.pack_forget()
        if result.failures:
            self._failures.delete(0, tk.END)
            for name, error in result.failures:
                self._failures.insert(tk.END, f"{name}: {error}")
            self._failures.pack(fill="both", expand=True, pady=8)
        self._open_button.state(["!disabled"] if result.output_dir else ["disabled"])

    def _open(self) -> None:
        result = self.app.last_result
        if result and result.output_dir and result.output_dir.exists():
            open_folder(result.output_dir)

    def _restart(self) -> None:
        self.app.input_path, self.app.videos, self.app.last_result = None, [], None
        self.app.show(HomeScreen)


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - explicit user action
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)])  # noqa: S603


def launch() -> None:
    configure_logging(APP_NAME)
    VideoThumbApp().mainloop()
