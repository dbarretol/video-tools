import errno

import av
import pytest

from videocore import (
    NoFramesError,
    NoVideoStreamError,
    VideoOpenError,
    hint_for,
    probe,
    run_parallel,
    sample_frames,
)
from videocore.verify import OutputError


def caused_by(cause: BaseException, exc: BaseException) -> BaseException:
    try:
        raise exc from cause
    except BaseException as raised:  # noqa: BLE001
        return raised


def winerror(code: int) -> OSError:
    error = OSError(errno.EIO, "failed")
    error.winerror = code
    return error


@pytest.mark.parametrize(
    ("exc", "words"),
    [
        (VideoOpenError("Could not open video: x"), "damaged"),
        (NoVideoStreamError("No video stream in x"), "audio file"),
        (NoFramesError("No frame could be decoded"), "codec"),
        (OutputError("x.jpg is empty"), "disk"),
        (FileNotFoundError("x"), "moved, renamed or deleted"),
        (PermissionError("x"), "access was denied"),
        (OSError(errno.ENOSPC, "No space left on device"), "the disk is full"),
        (winerror(112), "the disk is full"),
        (winerror(362), "OneDrive"),
    ],
)
def test_common_errors_come_with_a_hint(exc, words):
    assert words in hint_for(exc)


def test_unknown_errors_have_no_hint():
    assert hint_for(ValueError("something else")) is None
    assert hint_for(OSError(errno.EIO, "I/O error")) is None


def test_hints_are_found_in_the_causes():
    decoder = av.error.DecoderNotFoundError(errno.ENOENT, "decoder not found")
    assert "codec is not supported" in hint_for(caused_by(decoder, RuntimeError("wrapped")))


def test_cloud_only_files_explain_read_errors():
    assert "OneDrive" in hint_for(VideoOpenError("Could not open video"), offline=True)
    assert "codec" in hint_for(NoFramesError("none"), offline=True)


def test_no_video_stream_is_also_a_value_error():
    # Code that caught ValueError for audio-only files keeps working.
    assert issubclass(NoVideoStreamError, ValueError)
    assert issubclass(NoVideoStreamError, VideoOpenError)


def test_unreadable_videos_get_a_hint(tmp_path):
    broken = tmp_path / "x.mp4"
    broken.write_bytes(b"not a video")
    with pytest.raises(VideoOpenError) as raised:
        sample_frames(broken, [0])
    assert "damaged" in hint_for(raised.value)


def test_failed_items_carry_their_hint(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    (result,) = run_parallel(probe, [broken], workers=1)
    assert not result.ok
    assert "damaged" in result.hint
