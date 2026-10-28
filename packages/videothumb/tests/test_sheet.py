import pytest
from PIL import Image

from videocore import probe
from videothumb.sheet import (
    HEADER_HEIGHT,
    SheetOptions,
    default_sheet_path,
    format_time,
    header_lines,
    make_sheet,
)


def test_sheet_size_is_grid_plus_header(clip, tmp_path):
    options = SheetOptions(columns=4, rows=3, thumbnail_width=80)
    path = make_sheet(clip, tmp_path / "sheet.jpg", options)
    with Image.open(path) as sheet:
        # 160x90 scaled to 80 px wide keeps the 16:9 aspect: 80x45.
        assert sheet.size == (4 * 80, 3 * 45 + HEADER_HEIGHT)


@pytest.mark.parametrize(("sampling", "expected"), [("keyframe", 10), ("precise", 15)])
def test_sampling_mode(clip, tmp_path, gray_index, sampling, expected):
    # A single cell sits at the middle of the 3 s clip (1.5 s); keyframes are every second.
    options = SheetOptions(columns=1, rows=1, thumbnail_width=160, sampling=sampling)
    path = make_sheet(clip, tmp_path / "sheet.jpg", options)
    with Image.open(path) as sheet:
        assert gray_index(sheet, (80, HEADER_HEIGHT + 45)) == expected


def test_output_folder_is_created(clip, tmp_path):
    path = make_sheet(clip, tmp_path / "a" / "b" / "sheet.jpg", SheetOptions(columns=2, rows=1))
    assert path.is_file()


def test_video_without_video_stream(audio_only, tmp_path):
    with pytest.raises(ValueError, match="No video stream"):
        make_sheet(audio_only, tmp_path / "sheet.jpg")


def test_header_lines(clip):
    title, details = header_lines(probe(clip))
    assert title == "clip.mp4"
    assert "Resolution: 160x90" in details
    assert "Codec: h264" in details


def test_format_time():
    assert format_time(125.9) == "02:05"
    assert format_time(3725, long=True) == "01:02:05"


def test_default_sheet_path(tmp_path):
    assert default_sheet_path(tmp_path / "movie.mkv") == tmp_path / "movie_sheet.jpg"


@pytest.mark.parametrize(
    "kwargs",
    [{"columns": 0}, {"rows": 0}, {"thumbnail_width": 8}, {"jpeg_quality": 0}],
)
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        SheetOptions(**kwargs)
