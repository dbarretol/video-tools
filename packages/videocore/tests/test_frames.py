import threading

import pytest

from videocore import VideoOpenError, evenly_spaced, iter_frames, sample_frames

TOLERANCE = 4


@pytest.fixture
def frame_index(spec):
    """Recover the frame index from the flat gray level of a decoded frame."""

    def recover(image) -> int:
        gray = image.convert("L").getpixel((image.width // 2, image.height // 2))
        index = round(gray / spec.gray_step)
        assert abs(gray - spec.gray_for(index)) <= TOLERANCE, f"unexpected gray level {gray}"
        return index

    return recover


def test_precise_mode_returns_the_frame_at_each_timestamp(video_with_audio, frame_index):
    timestamps = [0.0, 0.3, 1.2, 1.9]
    frames = sample_frames(video_with_audio, timestamps, mode="precise")
    assert [frame_index(f) for f in frames] == [0, 3, 12, 19]


def test_keyframe_mode_returns_the_previous_keyframe(video_with_audio, spec, frame_index):
    timestamps = [0.3, 1.2, 1.9]
    frames = sample_frames(video_with_audio, timestamps, mode="keyframe")
    keyframe_period = spec.gop / spec.fps
    expected = [int(ts // keyframe_period) * spec.gop for ts in timestamps]
    assert [frame_index(f) for f in frames] == expected


def test_results_follow_input_order_even_if_unsorted(video_only, frame_index):
    frames = sample_frames(video_only, [1.5, 0.0, 0.8], mode="precise")
    assert [frame_index(f) for f in frames] == [15, 0, 8]


@pytest.mark.parametrize("mode", ["keyframe", "precise"])
@pytest.mark.parametrize("workers", [1, 3])
def test_parallel_workers_give_identical_results(video_with_audio, frame_index, mode, workers):
    timestamps = [1.9, 0.1, 1.1, 0.6, 1.4, 0.0, 0.9]
    serial = sample_frames(video_with_audio, timestamps, mode=mode, workers=1)
    parallel = sample_frames(video_with_audio, timestamps, mode=mode, workers=workers)
    assert [frame_index(f) for f in parallel] == [frame_index(f) for f in serial]


@pytest.mark.parametrize("mode", ["keyframe", "precise"])
def test_draft_decoding_matches_on_a_clip_without_b_frames(video_with_audio, frame_index, mode):
    # Every frame of the test clip is a reference frame, so draft skips none of them.
    timestamps = [0.0, 0.3, 1.2, 1.9]
    full = sample_frames(video_with_audio, timestamps, mode=mode)
    draft = sample_frames(video_with_audio, timestamps, mode=mode, draft=True)
    assert [frame_index(f) for f in draft] == [frame_index(f) for f in full]


@pytest.mark.parametrize("workers", [1, 3])
def test_iter_frames_yields_in_timestamp_order(video_only, frame_index, workers):
    timestamps = [1.5, 0.0, 0.8, 0.4, 1.1]
    pairs = list(iter_frames(video_only, timestamps, mode="precise", workers=workers))
    assert [index for index, _ in pairs] == [1, 3, 2, 4, 0]
    assert [frame_index(image) for _, image in pairs] == [0, 4, 8, 11, 15]


def test_iter_frames_can_stop_early_without_leaking_threads(video_only):
    before = threading.active_count()
    frames = iter_frames(video_only, [i / 10 for i in range(20)], workers=3)
    next(frames)
    frames.close()
    assert threading.active_count() == before


def test_iter_frames_raises_worker_errors(tmp_path):
    bogus = tmp_path / "bogus.mkv"
    bogus.write_bytes(b"not a video")
    with pytest.raises(VideoOpenError):
        list(iter_frames(bogus, [0.0, 0.5, 1.0], workers=3))


def test_iter_frames_validates_mode_immediately(video_only):
    with pytest.raises(ValueError):
        iter_frames(video_only, [0.0], mode="fast")


def test_empty_timestamps(video_only):
    assert sample_frames(video_only, []) == []


def test_size_scales_inside_the_decoder(video_only):
    (frame,) = sample_frames(video_only, [0.5], size=(80, 60))
    assert frame.size == (80, 60)
    assert frame.mode == "RGB"


def test_default_size_is_native_resolution(video_only, spec):
    (frame,) = sample_frames(video_only, [0.5])
    assert frame.size == (spec.width, spec.height)


def test_timestamp_past_the_end_still_returns_a_frame(video_only):
    (frame,) = sample_frames(video_only, [60.0], mode="precise")
    assert frame is not None


def test_unknown_mode(video_only):
    with pytest.raises(ValueError):
        sample_frames(video_only, [0.0], mode="fast")


def test_invalid_file(tmp_path):
    bogus = tmp_path / "bogus.mkv"
    bogus.write_bytes(b"not a video")
    with pytest.raises(VideoOpenError):
        sample_frames(bogus, [0.0])


def test_evenly_spaced_with_endpoints():
    assert evenly_spaced(1.0, 9.0, 5) == [1.0, 3.0, 5.0, 7.0, 9.0]
    assert evenly_spaced(2.0, 8.0, 1) == [2.0]


def test_evenly_spaced_without_endpoints():
    assert evenly_spaced(0.0, 10.0, 4, endpoints=False) == [2.0, 4.0, 6.0, 8.0]


def test_evenly_spaced_rejects_non_positive_count():
    with pytest.raises(ValueError):
        evenly_spaced(0.0, 1.0, 0)
