import hashlib
import json

import pytest

from videocore import probe
from videosemanticreducer.fingerprint import (
    FORMAT_VERSION,
    KEY_POSITIONS,
    IncompleteFingerprintError,
    check_complete,
    compute_fingerprint,
    compute_sha256,
    load_fingerprint,
    save_fingerprint,
)


@pytest.fixture(scope="module")
def clip_fingerprint(clip):
    return compute_fingerprint(probe(clip))


def test_sha256_matches_hashlib(clip):
    assert compute_sha256(clip) == hashlib.sha256(clip.read_bytes()).hexdigest()


def test_fingerprint_has_every_layer(clip, clip_fingerprint):
    fp = clip_fingerprint
    assert fp["version"] == FORMAT_VERSION == "2.0"
    assert fp["filename"] == "clip.mp4"
    assert fp["file_size_bytes"] == clip.stat().st_size
    assert fp["duration_seconds"] == pytest.approx(4.0, abs=0.1)

    global_hashes = fp["global_hashes"]
    for name in ("phash_global", "dhash_global", "ahash_global", "whash_global"):
        assert len(global_hashes[name]) == 64  # 256 bits in hex
    assert global_hashes["collage_frames"] == 64
    assert global_hashes["frame_size"] == 144

    assert [kf["position_pct"] for kf in fp["keyframe_hashes"]] == list(KEY_POSITIONS)
    assert all(len(kf["phash"]) == len(kf["dhash"]) == 16 for kf in fp["keyframe_hashes"])


def test_fingerprint_is_deterministic(clip, clip_fingerprint):
    assert compute_fingerprint(probe(clip)) == clip_fingerprint


def test_keyframe_hashes_change_along_the_video(clip_fingerprint):
    phashes = {kf["phash"] for kf in clip_fingerprint["keyframe_hashes"]}
    assert len(phashes) > len(KEY_POSITIONS) // 2


def test_precomputed_sha256_is_used(clip):
    fp = compute_fingerprint(probe(clip), sha256="abc")
    assert fp["sha256"] == "abc"


def test_audio_only_file_cannot_be_fingerprinted(audio_only):
    with pytest.raises(ValueError, match="No video stream"):
        compute_fingerprint(probe(audio_only))


def test_complete_fingerprint_passes_the_check(clip_fingerprint):
    check_complete(clip_fingerprint)


def test_fingerprint_without_global_hashes_is_incomplete(clip_fingerprint):
    with pytest.raises(IncompleteFingerprintError, match="No frame could be hashed"):
        check_complete({**clip_fingerprint, "global_hashes": None})


@pytest.mark.parametrize(("kept", "complete"), [(11, True), (10, False), (0, False)])
def test_fingerprint_needs_half_of_the_keyframe_positions(clip_fingerprint, kept, complete):
    # 22 positions: 11 is exactly half.
    assert len(KEY_POSITIONS) == 22
    partial = {**clip_fingerprint, "keyframe_hashes": clip_fingerprint["keyframe_hashes"][:kept]}
    if complete:
        check_complete(partial)
    else:
        with pytest.raises(IncompleteFingerprintError, match=f"Only {kept}/22"):
            check_complete(partial)


def test_compute_fingerprint_fails_when_no_frame_decodes(clip, monkeypatch):
    from videosemanticreducer import fingerprint

    monkeypatch.setattr(
        fingerprint, "sample_frames", lambda path, timestamps, **kwargs: [None] * len(timestamps)
    )
    with pytest.raises(IncompleteFingerprintError):
        compute_fingerprint(probe(clip), sha256="abc")


def test_save_and_load_round_trip(clip_fingerprint, tmp_path):
    path = save_fingerprint(clip_fingerprint, tmp_path / "clip.fingerprint.json")
    assert load_fingerprint(path) == clip_fingerprint


def test_load_rejects_missing_fields(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": "2.0", "sha256": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="global_hashes, keyframe_hashes"):
        load_fingerprint(path)


def test_load_rejects_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_fingerprint(path)


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_fingerprint(tmp_path / "missing.json")
