import hashlib
import json

import pytest

from videocore import probe
from videosemanticreducer.fingerprint import (
    FORMAT_VERSION,
    KEY_POSITIONS,
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


def test_audio_only_file_has_no_perceptual_layers(audio_only):
    fp = compute_fingerprint(probe(audio_only))
    assert fp["global_hashes"] is None
    assert fp["keyframe_hashes"] == []
    assert len(fp["sha256"]) == 64


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
