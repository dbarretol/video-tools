import copy

import pytest

from videosemanticreducer.compare import compare_fingerprints, hamming_distance, match_level

POSITIONS = (10, 50, 90)


def make_fingerprint(*, version="2.0", sha="a" * 64, duration=100.0, bit=0, kf_bits=0):
    """Fingerprint whose hashes have the lowest `bit`/`kf_bits` bits flipped."""
    global_hash = f"{(1 << bit) - 1:064x}"
    return {
        "version": version,
        "filename": "movie.mp4",
        "duration_seconds": duration,
        "sha256": sha,
        "global_hashes": {
            name: global_hash
            for name in ("phash_global", "dhash_global", "ahash_global", "whash_global")
        },
        "keyframe_hashes": [
            {"position_pct": pct, "phash": f"{(1 << kf_bits) - 1:016x}", "dhash": "0" * 16}
            for pct in POSITIONS
        ],
    }


def test_hamming_distance():
    assert hamming_distance("ff", "00") == 8
    assert hamming_distance("0f", "0f") == 0
    assert hamming_distance("zz", "00") is None
    assert hamming_distance(None, "00") is None


def test_same_file_is_exact():
    report = compare_fingerprints(make_fingerprint(), make_fingerprint())
    assert report.match_level == "EXACT"
    assert report.confidence == 100.0
    assert report.is_match


def test_reencoded_copy_is_very_high():
    report = compare_fingerprints(
        make_fingerprint(), make_fingerprint(sha="b" * 64, bit=5, kf_bits=3)
    )
    assert report.match_level == "VERY_HIGH"
    assert report.global_similarity == pytest.approx((1 - 5 / 256) * 100, abs=0.01)
    assert report.keyframes_exact_pct == 100.0
    assert report.is_match


def test_different_video_is_none():
    report = compare_fingerprints(
        make_fingerprint(), make_fingerprint(sha="b" * 64, bit=128, kf_bits=40)
    )
    assert report.match_level == "NONE"
    assert not report.is_match


def test_duration_mismatch_rejects_visual_matches():
    report = compare_fingerprints(make_fingerprint(), make_fingerprint(sha="b" * 64, duration=150))
    assert not report.duration_match
    assert report.match_level == "NONE"


def test_sha_match_wins_over_duration_mismatch():
    report = compare_fingerprints(make_fingerprint(), make_fingerprint(duration=150))
    assert report.match_level == "EXACT"


def test_global_hashes_are_ignored_across_major_versions():
    old = make_fingerprint(version="1.0", bit=200)
    new = make_fingerprint(sha="b" * 64)
    report = compare_fingerprints(old, new)
    assert report.global_similarity is None
    assert report.keyframes_close_pct == 100.0
    assert report.match_level == "VERY_HIGH"


def test_missing_version_is_treated_as_1_0():
    old = make_fingerprint()
    del old["version"]
    report = compare_fingerprints(old, make_fingerprint(version="1.0", sha="b" * 64))
    assert report.global_similarity is not None


def test_missing_global_hashes():
    fp = make_fingerprint(sha="b" * 64)
    fp["global_hashes"] = None
    report = compare_fingerprints(make_fingerprint(), fp)
    assert report.global_similarity is None


def test_keyframes_are_matched_by_position():
    fp = make_fingerprint(sha="b" * 64)
    other = copy.deepcopy(fp)
    other["sha256"] = "c" * 64
    other["keyframe_hashes"] = other["keyframe_hashes"][:1]
    report = compare_fingerprints(fp, other)
    assert report.keyframes_close_pct == 100.0


@pytest.mark.parametrize(
    ("global_sim", "kf_close", "expected"),
    [
        (99.0, 90.0, "VERY_HIGH"),
        (90.0, 75.0, "HIGH"),
        (80.0, 10.0, "MEDIUM"),
        (50.0, 40.0, "LOW"),
        (50.0, 10.0, "NONE"),
        (None, 96.0, "VERY_HIGH"),
        (None, 90.0, "HIGH"),
        (None, 60.0, "MEDIUM"),
        (None, 35.0, "LOW"),
        (None, 0.0, "NONE"),
    ],
)
def test_match_level_thresholds(global_sim, kf_close, expected):
    assert match_level(False, global_sim, kf_close)[0] == expected
