import re
import zipfile

import pytest
from PIL import Image

from videocore.verify import OutputError
from videothumb import cli, pack
from videothumb.pack import PackCounts, PackOptions, check_pack, default_pack_path, make_pack

FRAME = re.compile(r"frames/clip_f(\d{3})_00h00m0\ds\.jpg")
SMALL = PackOptions(columns=2, rows=2, thumbnail_width=80)


def entries(path):
    with zipfile.ZipFile(path) as archive:
        return archive.infolist()


def test_a_pack_holds_the_sheet_and_one_frame_per_cell(clip, tmp_path, monkeypatch):
    def no_second_decode(*args, **kwargs):
        raise AssertionError("the frames should fill the sheet")

    monkeypatch.setattr(pack, "sample_frames", no_second_decode)
    out = make_pack(clip, tmp_path / "clip_pack.zip", SMALL)

    infos = entries(out)
    names = [info.filename for info in infos]
    assert names[-1] == "clip_sheet.jpg"
    assert [int(FRAME.fullmatch(name).group(1)) for name in names[:-1]] == [1, 2, 3, 4]
    assert all(info.compress_type == zipfile.ZIP_STORED for info in infos)
    with zipfile.ZipFile(out) as archive:
        with archive.open("clip_sheet.jpg") as file, Image.open(file) as sheet:
            assert sheet.width == 2 * 80
        with archive.open(names[0]) as file, Image.open(file) as frame:
            assert frame.size == (160, 90)  # full size
    check_pack(out, "clip", SMALL, 4)
    assert not list(tmp_path.glob("*.part"))


def test_another_count_samples_the_sheet_separately(clip, tmp_path):
    options = PackOptions(columns=2, rows=2, thumbnail_width=80, count=3)
    counts = PackCounts(pack.FrameCount(), pack.FrameCount())
    out = make_pack(clip, tmp_path / "p.zip", options, counts=counts, name="clip.mp4")
    names = [info.filename for info in entries(out)]
    assert len([name for name in names if name.startswith("frames/clip.mp4_f")]) == 3
    assert "clip.mp4_sheet.jpg" in names
    assert (counts.frames.decoded, counts.frames.total) == (3, 3)
    assert (counts.sheet.decoded, counts.sheet.total) == (4, 4)


def test_frames_that_cannot_be_decoded_are_reported(clip, tmp_path, monkeypatch, caplog):
    real = pack.iter_frames

    def lose_second_frame(*args, **kwargs):
        for index, image in real(*args, **kwargs):
            yield index, None if index == 1 else image

    monkeypatch.setattr(pack, "iter_frames", lose_second_frame)
    counts = PackCounts(pack.FrameCount(), pack.FrameCount())
    out = make_pack(clip, tmp_path / "p.zip", SMALL, counts=counts)
    assert [info.filename.split("_")[1] for info in entries(out)][:3] == ["f001", "f003", "f004"]
    assert counts.frames.decoded == 3 and counts.sheet.decoded == 3
    assert "has 3/4 frames and 3/4 sheet cells" in caplog.text


def test_check_pack_finds_what_is_wrong(clip, tmp_path):
    out = make_pack(clip, tmp_path / "p.zip", SMALL)
    with pytest.raises(OutputError, match="has 4 frames, expected 5"):
        check_pack(out, "clip", SMALL, 5)
    with pytest.raises(OutputError, match="160 pixels wide, expected 240"):
        check_pack(out, "clip", PackOptions(columns=3, rows=2, thumbnail_width=80), 4)
    with pytest.raises(OutputError, match="has no other_sheet.jpg"):
        check_pack(out, "other", SMALL, 4)
    out.write_bytes(b"not a zip")
    with pytest.raises(OutputError, match="not a readable ZIP"):
        check_pack(out, "clip", SMALL, 4)


def test_video_without_video_stream(audio_only, tmp_path):
    with pytest.raises(ValueError, match="No video stream"):
        make_pack(audio_only, tmp_path / "p.zip")
    assert not list(tmp_path.iterdir())


def test_default_pack_path(tmp_path):
    assert default_pack_path(tmp_path / "movie.mp4") == tmp_path / "movie_pack.zip"


def test_count_defaults_to_the_cells():
    assert PackOptions(columns=3, rows=2).frame_count == 6
    assert PackOptions(columns=3, rows=2, count=10).frame_count == 10
    with pytest.raises(ValueError, match="count must be greater than 0"):
        PackOptions(count=0)


def test_cli_pack_one_video_and_a_folder(clip, folder, tmp_path, capsys):
    out = tmp_path / "one.zip"
    args = ["pack", str(clip), "-o", str(out), "--columns", "2", "--rows", "2"]
    assert cli.main(args) == 0
    assert f"Pack saved to {out}" in capsys.readouterr().out
    assert len(entries(out)) == 5

    args = ["pack", str(folder), "-w", "1", "--count", "2", "--thumbnail-width", "40"]
    assert cli.main(args) == 1  # the folder has a broken video too
    for name in ("a", "b"):
        names = [info.filename for info in entries(folder / f"{name}_pack.zip")]
        assert len(names) == 3 and f"{name}_sheet.jpg" in names

    capsys.readouterr()
    assert cli.main([*args, "--skip-existing"]) == 1
    assert "2 skipped" in capsys.readouterr().out


def test_cli_incomplete_pack_exits_with_3(clip, tmp_path, monkeypatch):
    real = pack.iter_frames

    def lose_first_frame(*args, **kwargs):
        for index, image in real(*args, **kwargs):
            yield index, None if index == 0 else image

    monkeypatch.setattr(pack, "iter_frames", lose_first_frame)
    args = ["pack", str(clip), "-o", str(tmp_path / "p.zip"), "--columns", "1", "--rows", "2"]
    assert cli.main(args) == 3
    assert cli.main([*args, "--strict"]) == 1


def test_cli_pack_takes_the_options_of_both(clip, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["pack", str(clip), "--bogus"])
    assert exc.value.code == 2
    help_text = cli.build_parser()._subparsers._group_actions[0].choices["pack"].format_help()
    for flag in ("--columns", "--rows", "--thumbnail-width", "--count", "--quality", "--precise"):
        assert flag in help_text
