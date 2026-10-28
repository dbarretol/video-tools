from pathlib import Path

import av
import pytest
from PIL import Image

from videosemanticreducer.pipeline import (
    INFO_CARD_DURATION,
    ProcessOptions,
    _grid_sources,
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


def test_audio_only_gets_metadata_and_fingerprint(audio_only, tmp_path):
    result = process_video(audio_only, tmp_path)
    assert sorted(p.name for p in result.outputs) == ["audio.fingerprint.json", "audio_info.txt"]


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
