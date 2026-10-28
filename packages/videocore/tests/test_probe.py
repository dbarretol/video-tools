import pytest

from videocore import VideoOpenError, format_hms, probe


def test_probe_reads_video_and_audio(video_with_audio, spec):
    info = probe(video_with_audio)

    assert info.filename == "with_audio.mp4"
    assert info.size_bytes == video_with_audio.stat().st_size
    assert info.duration == pytest.approx(spec.frames / spec.fps, abs=0.15)
    assert "mp4" in info.format_name
    assert info.tags.get("title") == "videocore test clip"

    assert info.has_video
    assert info.video.codec == "h264"
    assert info.video.resolution == f"{spec.width}x{spec.height}"
    assert info.video.fps == pytest.approx(spec.fps)
    assert info.video.frame_count == spec.frames

    assert info.audio is not None
    assert info.audio.codec == "aac"
    assert info.audio.sample_rate == 48000
    assert info.audio.channels == 1


def test_probe_without_audio_and_estimated_frame_count(video_only, spec):
    info = probe(video_only)

    assert info.audio is None
    assert info.format_name.startswith("matroska")
    # Matroska does not store a frame count; it is estimated from duration and fps.
    assert info.video.frame_count == pytest.approx(spec.frames, abs=1)


def test_probe_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe(tmp_path / "missing.mp4")


def test_probe_invalid_file(tmp_path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(b"this is not a video")
    with pytest.raises(VideoOpenError):
        probe(bogus)


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0, "00:00:00"), (59.9, "00:00:59"), (3662.9, "01:01:02")]
)
def test_format_hms(seconds, expected):
    assert format_hms(seconds) == expected
