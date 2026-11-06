from pathlib import Path

import av
import pytest
from PIL import Image

from videocore import RunOptions, Status, run_task
from videocore.verify import OutputError
from videosemanticreducer.manifest import is_complete, read_manifest
from videosemanticreducer.pipeline import (
    ARCHIVE,
    INFO_CARD_DURATION,
    ProcessOptions,
    _grid_sources,
    archive_state,
    is_output_dir,
    process_video,
    resolve_output_dir,
    snapshot_timestamps,
    verify_artifacts,
)

SMALL = ProcessOptions(num_snapshots=12, thumb_cols=4, thumb_rows=3)
QUICK = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)


def archive(videos, output=None, options=SMALL, *, overwrite=False, **kwargs):
    run = RunOptions(output=output, workers=1, overwrite=overwrite)
    return run_task(ARCHIVE, videos, options, run, **kwargs)


def video_frame_count(path: Path) -> int:
    with av.open(str(path)) as container:
        return sum(1 for _ in container.decode(video=0))


def test_process_creates_every_artifact(clip, tmp_path):
    result = process_video(clip, tmp_path / "out", SMALL)

    names = sorted(p.name for p in result.outputs)
    assert names == [
        "clip.fingerprint.json",
        "clip_info.txt",
        "clip_summary.mp4",
        "clip_thumb.jpg",
    ]
    assert all(p.is_file() and p.parent == tmp_path / "out" for p in result.outputs)
    assert {"probe", "snapshots", "thumbnail", "global_hashes", "total"} <= result.timings.keys()


def test_manifest_records_a_complete_archive(clip, tmp_path):
    result = process_video(clip, tmp_path / "out", SMALL)

    assert result.manifest == tmp_path / "out" / "clip.archive.json"
    manifest = read_manifest(result.manifest)
    assert manifest["complete"] is True
    assert manifest["error"] is None
    assert manifest["source"]["filename"] == "clip.mp4"
    assert manifest["source"]["size_bytes"] == clip.stat().st_size
    assert len(manifest["source"]["sha256"]) == 64
    assert manifest["options"]["num_snapshots"] == 12
    assert "sampling_workers" not in manifest["options"]
    listed = {entry["file"]: entry for entry in manifest["outputs"]}
    assert sorted(listed) == sorted(p.name for p in result.outputs)
    thumb = tmp_path / "out" / "clip_thumb.jpg"
    assert listed["clip_thumb.jpg"]["size_bytes"] == thumb.stat().st_size
    assert is_complete(result.manifest)


def test_manifest_records_a_failed_archive(clip, tmp_path, monkeypatch):
    from videosemanticreducer import fingerprint

    monkeypatch.setattr(fingerprint, "compute_global_hashes", lambda info, **kwargs: None)
    options = ProcessOptions(summary=False, thumb_cols=2, thumb_rows=2)

    with pytest.raises(fingerprint.IncompleteFingerprintError):
        process_video(clip, tmp_path, options)

    manifest = read_manifest(tmp_path / "clip.archive.json")
    assert manifest["complete"] is False
    assert manifest["error"].startswith("IncompleteFingerprintError")
    assert [entry["file"] for entry in manifest["outputs"]] == ["clip_info.txt", "clip_thumb.jpg"]
    assert not is_complete(tmp_path / "clip.archive.json")


def test_rerun_replaces_the_previous_manifest(clip, tmp_path, monkeypatch):
    from videosemanticreducer import pipeline

    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)
    process_video(clip, tmp_path, options)
    assert is_complete(tmp_path / "clip.archive.json")

    def killed(*args, **kwargs):
        raise KeyboardInterrupt  # not an Exception: no manifest is written at all

    monkeypatch.setattr(pipeline, "_write_artifacts", killed)
    with pytest.raises(KeyboardInterrupt):
        process_video(clip, tmp_path, options)
    assert not (tmp_path / "clip.archive.json").exists()


def test_manifest_is_incomplete_when_an_output_changes(clip, tmp_path):
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)
    result = process_video(clip, tmp_path, options)

    (tmp_path / "clip_thumb.jpg").write_bytes(b"truncated")
    assert not is_complete(result.manifest)
    (tmp_path / "clip_thumb.jpg").unlink()
    assert not is_complete(result.manifest)


def test_is_complete_tolerates_bad_manifests(tmp_path):
    assert not is_complete(tmp_path / "missing.archive.json")
    (tmp_path / "bad.archive.json").write_text("{not json")
    assert not is_complete(tmp_path / "bad.archive.json")
    (tmp_path / "list.archive.json").write_text("[]")
    assert not is_complete(tmp_path / "list.archive.json")


def test_leftover_part_files_are_removed(clip, tmp_path):
    (tmp_path / "clip_summary.mp4.part").write_bytes(b"killed run")
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)
    process_video(clip, tmp_path, options)
    assert not list(tmp_path.glob("*.part"))


def test_batch_removes_part_files_of_cancelled_videos(clip, tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(clip.read_bytes())
    leftover = tmp_path / "b_summary" / "b_summary.mp4.part"
    leftover.parent.mkdir()
    leftover.write_bytes(b"killed worker")

    report = archive(videos, should_cancel=lambda: True)

    assert [r.status for r in report.items] == [Status.CANCELLED, Status.CANCELLED]
    assert not leftover.exists()


def test_archive_state(clip, tmp_path):
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)
    assert archive_state(tmp_path, "clip", options) is None
    process_video(clip, tmp_path, options)

    assert archive_state(tmp_path, "clip", options) == "complete"
    other_grid = ProcessOptions(summary=False, fingerprint=False, thumb_cols=3, thumb_rows=2)
    assert archive_state(tmp_path, "clip", other_grid) == "outdated"
    # Fewer artifacts than were made is still complete; more is not.
    only_report = ProcessOptions(
        summary=False, thumbnail=False, fingerprint=False, thumb_cols=2, thumb_rows=2
    )
    assert archive_state(tmp_path, "clip", only_report) == "complete"
    with_fingerprint = ProcessOptions(summary=False, thumb_cols=2, thumb_rows=2)
    assert archive_state(tmp_path, "clip", with_fingerprint) is None


def test_batch_skips_complete_archives(clip, tmp_path):
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(clip.read_bytes())
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)
    process_video(videos[0], tmp_path / "a_summary", options)
    progress = []

    report = archive(
        videos, options=options, on_progress=lambda done, total, r: progress.append((done, total))
    )

    assert [(r.video.name, r.status) for r in report.items] == [
        ("a.mp4", Status.SKIPPED),
        ("b.mp4", Status.OK),
    ]
    assert progress == [(1, 1)]
    assert archive(videos, options=options, overwrite=True).count(Status.OK) == 2


def test_batch_reports_archives_made_with_other_settings(clip, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(clip.read_bytes())
    process_video(video, tmp_path / "a_summary", QUICK)
    other = ProcessOptions(summary=False, fingerprint=False, thumb_cols=3, thumb_rows=2)

    report = archive([video], options=other)

    assert report.items[0].status is Status.OUTDATED and report.items[0].ok


def test_summary_has_info_card_then_one_frame_per_snapshot(clip, tmp_path):
    process_video(clip, tmp_path, SMALL)
    card_frames = round(INFO_CARD_DURATION / SMALL.snap_duration)
    assert video_frame_count(tmp_path / "clip_summary.mp4") == card_frames + 12


def test_thumbnail_grid_size(clip, tmp_path):
    process_video(clip, tmp_path, SMALL)
    with Image.open(tmp_path / "clip_thumb.jpg") as grid:
        # 160x90 is below the 240 px thumbnail size, so cells keep the native size.
        assert grid.size == (4 * 160, 3 * 90)


def test_grid_sampled_separately_when_there_are_few_snapshots(clip, tmp_path):
    options = ProcessOptions(num_snapshots=5, thumb_cols=4, thumb_rows=3)
    result = process_video(clip, tmp_path, options)
    assert tmp_path / "clip_thumb.jpg" in result.outputs


def test_options_skip_artifacts(clip, tmp_path):
    options = ProcessOptions(summary=False, thumbnail=True, fingerprint=False, **_small_grid())
    result = process_video(clip, tmp_path, options)
    assert sorted(p.name for p in result.outputs) == ["clip_info.txt", "clip_thumb.jpg"]


def test_only_metadata(clip, tmp_path):
    options = ProcessOptions(summary=False, thumbnail=False, fingerprint=False)
    result = process_video(clip, tmp_path, options)
    assert [p.name for p in result.outputs] == ["clip_info.txt"]
    assert "snapshots" not in result.timings


def test_audio_only_fails_without_writing_anything(audio_only, tmp_path):
    with pytest.raises(ValueError, match="No video stream"):
        process_video(audio_only, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_incomplete_fingerprint_fails_and_is_not_written(clip, tmp_path, monkeypatch):
    from videosemanticreducer import fingerprint

    monkeypatch.setattr(fingerprint, "compute_global_hashes", lambda info, **kwargs: None)
    options = ProcessOptions(summary=False, thumbnail=False)

    with pytest.raises(fingerprint.IncompleteFingerprintError):
        process_video(clip, tmp_path, options)
    assert not (tmp_path / "clip.fingerprint.json").exists()


def test_partial_thumbnail_grid_is_logged(clip, tmp_path, monkeypatch, caplog):
    from videosemanticreducer import pipeline

    real = pipeline.sample_frames

    def lose_first_frame(*args, **kwargs):
        frames = real(*args, **kwargs)
        return [None, *frames[1:]]

    monkeypatch.setattr(pipeline, "sample_frames", lose_first_frame)
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)

    process_video(clip, tmp_path, options)

    assert "has 3/4 cells" in caplog.text


def test_is_output_dir(tmp_path):
    own = tmp_path / "movie_summary"
    own.mkdir()
    (own / "movie_info.txt").touch()
    by_manifest = tmp_path / "third_summary"
    by_manifest.mkdir()
    (by_manifest / "third.archive.json").touch()
    by_fingerprint = tmp_path / "other_summary"
    by_fingerprint.mkdir()
    (by_fingerprint / "other.fingerprint.json").touch()
    lookalike = tmp_path / "trip_summary"  # a user folder that only shares the suffix
    lookalike.mkdir()
    (lookalike / "day1.mp4").touch()
    plain = tmp_path / "movie"
    plain.mkdir()
    (plain / "movie_info.txt").touch()

    assert is_output_dir(own)
    assert is_output_dir(by_manifest)
    assert is_output_dir(by_fingerprint)
    assert not is_output_dir(lookalike)
    assert not is_output_dir(plain)


def test_process_batch_isolates_failures(clip, tmp_path):
    good, broken = tmp_path / "good.mp4", tmp_path / "broken.mp4"
    good.write_bytes(clip.read_bytes())
    broken.write_bytes(b"not a video")
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)

    report = archive([good, broken], tmp_path / "out", options)

    ok, failed = report.items
    assert ok.status is Status.OK and ok.output == tmp_path / "out" / "good_summary"
    assert ok.value.timings["total"] > 0
    assert (tmp_path / "out" / "good_summary" / "good_thumb.jpg").is_file()
    assert failed.status is Status.FAILED and failed.error_type
    assert report.failed == [failed] and report.exit_code == 1


def test_batch_videos_sharing_a_stem_get_separate_folders(clip, tmp_path):
    for name in ("clip.mp4", "clip.mkv"):
        (tmp_path / name).write_bytes(clip.read_bytes())
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)

    report = archive([tmp_path / "clip.mkv", tmp_path / "clip.mp4"], options=options)

    assert [r.output for r in report.items] == [
        tmp_path / "clip.mkv_summary",
        tmp_path / "clip.mp4_summary",
    ]
    assert (tmp_path / "clip.mp4_summary" / "clip.mp4_thumb.jpg").is_file()
    assert (tmp_path / "clip.mkv_summary" / "clip.mkv_info.txt").is_file()
    assert is_output_dir(tmp_path / "clip.mp4_summary")


def test_batch_recreates_subfolders_in_the_output(clip, tmp_path):
    source, out = tmp_path / "videos", tmp_path / "out"
    for sub in ("x", "y"):
        (source / sub).mkdir(parents=True)
        (source / sub / "clip.mp4").write_bytes(clip.read_bytes())
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)
    videos = [source / "x" / "clip.mp4", source / "y" / "clip.mp4"]

    report = archive(videos, out, options, source=source)

    assert [r.output for r in report.items] == [
        out / "x" / "clip_summary",
        out / "y" / "clip_summary",
    ]


def test_batch_collision_fails_before_processing(clip, tmp_path):
    from videocore import OutputCollisionError

    videos = [tmp_path / "x" / "clip.mp4", tmp_path / "y" / "clip.mp4"]
    with pytest.raises(OutputCollisionError):
        archive(videos, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_resolve_output_dir():
    video = Path("videos/movie.mp4")
    assert resolve_output_dir(video, None, batch=False) == Path("videos/movie_summary")
    assert resolve_output_dir(video, None, batch=True) == Path("videos/movie_summary")
    assert resolve_output_dir(video, Path("out"), batch=False) == Path("out")
    assert resolve_output_dir(video, Path("out"), batch=True) == Path("out/movie_summary")


def test_snapshot_timestamps_skip_first_and_last_percent():
    assert snapshot_timestamps(100.0, 3) == [1.0, 50.0, 99.0]


def test_grid_sources_cover_every_cell_once():
    sources = _grid_sources(150, 144)
    cells = sorted(cell for group in sources.values() for cell in group)
    assert cells == list(range(144))
    assert sources[0] == [0] and sources[149] == [143]


@pytest.mark.parametrize(
    "kwargs",
    [{"num_snapshots": 0}, {"snap_duration": 0}, {"thumb_cols": 0}, {"thumb_rows": -1}],
)
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        ProcessOptions(**kwargs)


def _small_grid():
    return {"thumb_cols": 2, "thumb_rows": 2}


def test_archive_reports_its_stages(clip, tmp_path):
    events = []
    report = archive([clip], tmp_path / "out", on_event=events.append)

    assert report.items[0].status is Status.OK
    stages = list(dict.fromkeys(e.stage for e in events if e.kind == "stage"))
    for expected in ("probe", "snapshots", "thumbnail", "global_hashes", "keyframe_hashes"):
        assert expected in stages
    assert stages.index("probe") < stages.index("snapshots") < stages.index("global_hashes")
    assert stages[-1] == "manifest"
    snapshots = [e for e in events if e.stage == "snapshots" and e.total]
    assert snapshots[-1].done == snapshots[-1].total == SMALL.num_snapshots
    assert any(e.stage == "sha256" and e.done == e.total for e in events)


def test_cancelled_archive_leaves_no_partial_or_complete_record(clip, tmp_path, monkeypatch):
    from videocore import CancelToken, progress

    monkeypatch.setattr(progress, "STAGE_INTERVAL_SECONDS", 0.0)  # an event per snapshot
    token = CancelToken()

    def on_event(event):
        if event.stage == "snapshots" and event.done >= 3:
            token.cancel()

    out = tmp_path / "out"
    report = archive([clip], out, on_event=on_event, should_cancel=token)

    assert report.items[0].status is Status.CANCELLED
    folder = out / "clip_summary"
    assert not list(folder.glob("*.part"))
    assert not (folder / "clip_summary.mp4").exists()
    assert not (folder / "clip.archive.json").exists()  # never looks archived


def test_incomplete_grid_makes_a_degraded_archive_that_is_redone(clip, tmp_path, monkeypatch):
    from videosemanticreducer import pipeline

    real = pipeline.sample_frames

    def lose_frames(*args, **kwargs):
        frames = real(*args, **kwargs)
        return [frames[0], *[None] * (len(frames) - 1)]

    monkeypatch.setattr(pipeline, "sample_frames", lose_frames)
    report = archive([clip], tmp_path / "out", QUICK)

    (result,) = report.items
    assert result.status is Status.DEGRADED
    assert result.problems == ["thumbnail grid: only 1/4 frames could be decoded"]
    assert report.exit_code == 3
    manifest = read_manifest(tmp_path / "out" / "clip_summary" / "clip.archive.json")
    assert manifest["complete"] is False and manifest["problems"] == result.problems
    # Not complete, so the next run archives it again instead of skipping it.
    assert archive_state(tmp_path / "out" / "clip_summary", "clip", QUICK) is None


def test_artifacts_are_read_back(clip, tmp_path):
    result = process_video(clip, tmp_path, SMALL)
    assert result.problems == []
    assert verify_artifacts(result, SMALL) == []

    (tmp_path / "clip_summary.mp4").write_bytes(b"not a video any more")
    with pytest.raises(OutputError, match="clip_summary.mp4 is not a readable video"):
        verify_artifacts(result, SMALL)

    result.thumb_size = (1, 1)
    result.outputs = [p for p in result.outputs if p.name.endswith("_thumb.jpg")]
    with pytest.raises(OutputError, match="expected 1x1"):
        verify_artifacts(result, SMALL)


def test_an_unreadable_artifact_fails_the_archive(clip, tmp_path, monkeypatch):
    from videosemanticreducer import pipeline

    def broken(path, duration=None):
        raise OutputError(f"{path.name} lasts 0.0 s, expected about {duration:.1f} s")

    monkeypatch.setattr(pipeline, "check_video", broken)
    report = archive([clip], tmp_path / "out", SMALL)
    assert report.items[0].status is Status.FAILED
    assert "clip_summary.mp4 lasts 0.0 s, expected about" in report.items[0].error
    manifest = read_manifest(tmp_path / "out" / "clip_summary" / "clip.archive.json")
    assert manifest["complete"] is False and "OutputError" in manifest["error"]


def test_summary_and_grid_fall_back_to_precise_sampling(clip, tmp_path, monkeypatch):
    from videosemanticreducer import pipeline

    calls = []
    for name in ("iter_frames", "sample_frames"):
        real = getattr(pipeline, name)

        def spy(*args, _real=real, **kwargs):
            calls.append(kwargs.get("fallback"))
            return _real(*args, **kwargs)

        monkeypatch.setattr(pipeline, name, spy)
    process_video(clip, tmp_path, ProcessOptions(num_snapshots=4, fingerprint=False))
    assert calls == [True, True]  # summary snapshots, then the grid (sampled separately)
