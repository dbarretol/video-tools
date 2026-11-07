from videocore import probe
from videosemanticreducer.report import format_report, format_size, write_report


def test_format_size():
    assert format_size(512) == "512.00 B"
    assert format_size(1536) == "1.50 KB"
    assert format_size(3 * 1024**3) == "3.00 GB"


def test_report_describes_video_audio_and_tags(clip, tmp_path):
    info = probe(clip)
    path = write_report(info, tmp_path / "info.txt")
    text = path.read_text(encoding="utf-8")

    for section in ("=== FILE ===", "=== VIDEO ===", "=== AUDIO ===", "=== TAGS ==="):
        assert section in text
    assert "Name: clip.mp4" in text
    assert "Resolution: 160x90" in text
    assert "Frame rate: 10.00 fps" in text
    assert "Sample rate: 48000 Hz" in text
    assert "title: vsr test clip" in text


def test_report_of_audio_only_file(audio_only):
    text = format_report(probe(audio_only))
    assert "No video stream." in text
    assert "Channels: 1" in text
