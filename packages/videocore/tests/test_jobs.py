from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from videocore import OutputCollisionError, OutputSlot, RunOptions, Status, execute, jobs, plan
from videocore.batch import is_offline


@dataclass(frozen=True)
class Options:
    text: str = "hello"


@dataclass
class FakeTask:
    """Writes `options.text` to ``<name>.txt``; fails for videos named ``bad*``."""

    name: str = "fake"
    seconds: float | None = 2.0

    def __post_init__(self) -> None:
        self.cleaned: list[Path] = []

    def output(self, slot: OutputSlot) -> Path:
        return slot.folder / f"{slot.name}.txt"

    def state(self, output: Path, name: str, options: Options) -> Status | None:
        if not output.is_file():
            return None
        same = output.read_text(encoding="utf-8") == options.text
        return Status.SKIPPED if same else Status.OUTDATED

    def run(self, video, output, name, options, decoders):
        if video.name.startswith("bad"):
            raise ValueError(f"cannot read {video.name}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(options.text, encoding="utf-8")
        return decoders

    def cleanup(self, output: Path) -> None:
        self.cleaned.append(output)

    def estimate(self, duration: float, options: Options) -> float | None:
        return self.seconds


@pytest.fixture
def videos(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_duration", lambda video: 60.0)
    paths = [tmp_path / name for name in ("a.mp4", "b.mp4", "bad.mp4")]
    for path in paths:
        path.write_bytes(b"x")
    return paths


def test_plan_marks_done_and_outdated_items(videos, tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("other", encoding="utf-8")

    planned = plan(FakeTask(), videos, Options())

    assert [item.state for item in planned.items] == [Status.SKIPPED, Status.OUTDATED, None]
    assert [item.name for item in planned.pending] == ["bad"]
    assert planned.pending[0].duration == 60.0
    assert planned.items[0].duration is None  # only pending videos are measured

    redo = plan(FakeTask(), videos, Options(), RunOptions(overwrite=True))
    assert len(redo.pending) == 3


def test_plan_with_explicit_outputs(videos, tmp_path):
    planned = plan(FakeTask(), videos[:1], Options(), outputs=[tmp_path / "x" / "custom.txt"])
    assert planned.items[0].output == tmp_path / "x" / "custom.txt"
    assert planned.items[0].name == "a"
    with pytest.raises(ValueError, match="one path per video"):
        plan(FakeTask(), videos, Options(), outputs=[tmp_path / "x.txt"])


def test_plan_detects_collisions_before_writing(tmp_path):
    clashing = [tmp_path / "x" / "clip.mp4", tmp_path / "y" / "clip.mp4"]
    with pytest.raises(OutputCollisionError):
        plan(FakeTask(), clashing, Options(), RunOptions(output=tmp_path / "out"), measure=False)


def test_execute_reports_every_item_in_plan_order(videos, tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    task = FakeTask()
    progress = []

    report = execute(
        plan(task, videos, Options(), RunOptions(workers=1)),
        on_progress=lambda done, total, r: progress.append((done, total, r.video.name)),
    )

    assert [(r.video.name, r.status) for r in report.items] == [
        ("a.mp4", Status.SKIPPED),
        ("b.mp4", Status.OK),
        ("bad.mp4", Status.FAILED),
    ]
    assert progress == [(1, 2, "b.mp4"), (2, 2, "bad.mp4")]
    failed = report.failed[0]
    assert failed.error_type == "ValueError" and "cannot read bad.mp4" in failed.error
    assert failed.seconds is not None
    assert report.exit_code == 1 and not report.cancelled
    assert task.cleaned == [tmp_path / "b.txt", tmp_path / "bad.txt"]
    assert report.items[0].ok and report.items[1].ok and not failed.ok


def test_one_pending_video_gets_every_decoder(videos):
    report = execute(plan(FakeTask(), videos[:1], Options(), RunOptions(workers=4)))
    assert report.items[0].value is None  # decoders: let the task use several


def test_cancelled_items_are_reported(videos):
    task = FakeTask()
    report = execute(
        plan(task, videos, Options(), RunOptions(workers=1)), should_cancel=lambda: True
    )
    assert [r.status for r in report.items] == [Status.CANCELLED] * 3
    assert report.cancelled and report.exit_code == 0
    assert len(task.cleaned) == 3


def test_summary_describes_the_plan(videos, tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("other", encoding="utf-8")
    planned = plan(FakeTask(), videos, Options(), RunOptions(workers=2))

    assert planned.summary() == [
        "3 videos: 1 to process, 1 already done, 1 done with other settings "
        "(use --overwrite to redo them)",
        "Video to process: 00:01:00; estimated time: about 2 s with 1 in parallel",
        "Output: next to each video",
    ]
    assert (
        planned.item_lines()[1]
        == f"  [skip, done with other settings] {videos[1]} -> {tmp_path / 'b.txt'}"
    )
    relative = plan(
        FakeTask(), videos, Options(), RunOptions(output=tmp_path / "out"), source=tmp_path
    )
    assert relative.item_lines()[0] == f"  [process] a.mp4 -> {Path('out', 'a.txt')}"


def test_eta_is_unknown_without_an_estimate(videos, monkeypatch):
    planned = plan(FakeTask(seconds=None), videos, Options())
    assert planned.eta() is None
    assert "estimated" not in planned.summary()[1]

    monkeypatch.setattr(jobs, "_duration", lambda video: None)
    planned = plan(FakeTask(), videos, Options())
    assert planned.eta() is None
    assert planned.summary()[1] == "Video to process: unknown length"

    monkeypatch.setattr(jobs, "_duration", lambda video: None if video.name == "bad.mp4" else 90.0)
    line = plan(FakeTask(), videos, Options(), RunOptions(workers=1)).summary()[1]
    assert line == (
        "Video to process: 00:03:00 plus 1 of unknown length; "
        "estimated time: about 4 s with 1 in parallel"
    )


def test_eta_accounts_for_parallel_workers(videos):
    planned = plan(FakeTask(seconds=100.0), videos, Options(), RunOptions(workers=3))
    assert planned.workers == 3
    assert 100.0 <= planned.eta() < 300.0


def test_cloud_only_files_are_not_read_and_are_reported(videos, monkeypatch):
    read = []
    monkeypatch.setattr(jobs, "_duration", lambda video: read.append(video) or 60.0)
    monkeypatch.setattr(jobs, "is_offline", lambda video: video.name == "b.mp4")

    planned = plan(FakeTask(), videos, Options())

    assert videos[1] not in read
    assert planned.items[1].offline
    assert any("1 files are only in the cloud" in line for line in planned.summary())


def test_local_files_are_not_offline(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    assert not is_offline(path)
    assert not is_offline(tmp_path / "missing.mp4")


def test_run_options_are_validated():
    with pytest.raises(ValueError, match="workers must be greater than 0"):
        RunOptions(workers=0)
