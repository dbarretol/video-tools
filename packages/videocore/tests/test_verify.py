import pytest
from PIL import Image

from videocore.verify import FrameCount, OutputError, check_file, check_image, check_video


def test_frame_counts_below_the_threshold_are_problems():
    assert FrameCount(9, 10).problem("sheet") is None
    assert FrameCount(8, 10).problem("sheet") == "sheet: only 8/10 frames could be decoded"
    assert FrameCount(0, 0).problem("sheet") is None


def test_files_must_exist_and_not_be_empty(tmp_path):
    with pytest.raises(OutputError, match="a.txt was not written"):
        check_file(tmp_path / "a.txt")
    (tmp_path / "a.txt").write_bytes(b"")
    with pytest.raises(OutputError, match="a.txt is empty"):
        check_file(tmp_path / "a.txt")


def test_images_are_opened_and_measured(tmp_path):
    path = tmp_path / "a.jpg"
    Image.new("RGB", (40, 30)).save(path)
    assert check_image(path) == (40, 30)
    check_image(path, size=(40, 30))
    with pytest.raises(OutputError, match="a.jpg is 40x30, expected 20x30"):
        check_image(path, size=(20, 30))

    path.write_bytes(b"\xff\xd8 truncated")
    with pytest.raises(OutputError, match="a.jpg is not a readable image"):
        check_image(path)


def test_videos_are_probed(video_only, tmp_path):
    check_video(video_only)
    with pytest.raises(OutputError, match="expected about 500.0 s"):
        check_video(video_only, duration=500.0)
    broken = tmp_path / "b.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(OutputError, match="b.mp4 is not a readable video"):
        check_video(broken)


def test_videos_need_a_video_stream(video_with_audio, tmp_path):
    import av

    audio = tmp_path / "audio.m4a"
    with av.open(str(video_with_audio)) as source, av.open(str(audio), "w") as target:
        stream = target.add_stream_from_template(source.streams.audio[0])
        for packet in source.demux(source.streams.audio[0]):
            if packet.dts is not None:
                packet.stream = stream
                target.mux(packet)
    with pytest.raises(OutputError, match="no video stream"):
        check_video(audio)
