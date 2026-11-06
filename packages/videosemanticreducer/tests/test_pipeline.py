from pathlib import Path

import av
import pytest
from PIL import Image

from videosemanticreducer.manifest import is_complete, read_manifest
from videosemanticreducer.pipeline import (
    INFO_CARD_DURATION,
    ProcessOptions,
    _grid_sources,
    archive_state,
    is_output_dir,
    process_batch,
    process_video,
    resolve_output_dir,
    snapshot_timestamps,
)

SMALL = ProcessOptions(num_snapshots=12, thumb_cols=4, thumb_rows=3)


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

    results = process_batch(videos, None, SMALL, workers=1, should_cancel=lambda: True)

    assert results == []
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

    results = process_batch(
        videos,
        None,
        options,
        skip_complete=True,
        workers=1,
        on_progress=lambda done, total, r: progress.append((done, total)),
    )

    assert [(r.item.name, r.skipped, r.detail) for r in results] == [
        ("a.mp4", True, "complete"),
        ("b.mp4", False, None),
    ]
    assert progress == [(1, 1)]


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

    results = process_batch([good, broken], tmp_path / "out", options, workers=1)

    assert results[0].ok and results[0].value == tmp_path / "out" / "good_summary"
    assert (tmp_path / "out" / "good_summary" / "good_thumb.jpg").is_file()
    assert not results[1].ok


def test_batch_videos_sharing_a_stem_get_separate_folders(clip, tmp_path):
    for name in ("clip.mp4", "clip.mkv"):
        (tmp_path / name).write_bytes(clip.read_bytes())
    options = ProcessOptions(summary=False, fingerprint=False, thumb_cols=2, thumb_rows=2)

    results = process_batch(
        [tmp_path / "clip.mkv", tmp_path / "clip.mp4"], None, options, workers=1
    )

    assert [r.value for r in results] == [
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

    results = process_batch(videos, out, options, source=source, workers=1)

    assert [r.value for r in results] == [out / "x" / "clip_summary", out / "y" / "clip_summary"]


def test_batch_collision_fails_before_processing(clip, tmp_path):
    from videocore import OutputCollisionError

    videos = [tmp_path / "x" / "clip.mp4", tmp_path / "y" / "clip.mp4"]
    with pytest.raises(OutputCollisionError):
        process_batch(videos, tmp_path / "out", workers=1)
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
