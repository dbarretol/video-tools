"""Multi-layer comparison of two fingerprints."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

GLOBAL_HASH_TYPES = ("phash_global", "dhash_global", "ahash_global", "whash_global")
GLOBAL_HASH_BITS = 256
KEYFRAME_EXACT_DISTANCE = 4
KEYFRAME_CLOSE_DISTANCE = 10
MAX_DURATION_DIFF_PCT = 5.0

MATCH_LEVELS = ("EXACT", "VERY_HIGH", "HIGH", "MEDIUM", "LOW", "NONE")


@dataclass(frozen=True)
class Comparison:
    match_level: str
    confidence: float
    sha256_match: bool
    global_similarity: float | None
    """None when the global hashes are missing or come from incompatible format versions."""
    keyframes_exact_pct: float
    keyframes_close_pct: float
    duration_match: bool
    duration_diff_pct: float

    @property
    def is_match(self) -> bool:
        """True when the videos are the same with high confidence."""
        return self.match_level in ("EXACT", "VERY_HIGH")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def hamming_distance(hex1: str, hex2: str) -> int | None:
    """Number of differing bits between two hex hashes, or None if either is invalid."""
    try:
        return (int(hex1, 16) ^ int(hex2, 16)).bit_count()
    except (TypeError, ValueError):
        return None


def global_hashes_comparable(fp1: dict[str, Any], fp2: dict[str, Any]) -> bool:
    """Global hashes from different major versions sample different frames."""
    return _major(fp1.get("version")) == _major(fp2.get("version"))


def compare_fingerprints(fp1: dict[str, Any], fp2: dict[str, Any]) -> Comparison:
    d1 = fp1.get("duration_seconds") or 0
    d2 = fp2.get("duration_seconds") or 0
    duration_diff_pct = abs(d1 - d2) / max(d1, d2) * 100 if d1 > 0 and d2 > 0 else 0.0
    duration_match = duration_diff_pct <= MAX_DURATION_DIFF_PCT

    sha256_match = bool(fp1.get("sha256")) and fp1.get("sha256") == fp2.get("sha256")

    global_sim = None
    gh1, gh2 = fp1.get("global_hashes"), fp2.get("global_hashes")
    if gh1 and gh2 and global_hashes_comparable(fp1, fp2):
        global_sim = sum(
            _similarity(gh1.get(name), gh2.get(name), GLOBAL_HASH_BITS)
            for name in GLOBAL_HASH_TYPES
        ) / len(GLOBAL_HASH_TYPES)

    kf_exact_pct, kf_close_pct = _keyframe_scores(
        fp1.get("keyframe_hashes") or [], fp2.get("keyframe_hashes") or []
    )
    level, confidence = match_level(sha256_match, global_sim, kf_close_pct, duration_match)
    return Comparison(
        match_level=level,
        confidence=round(confidence, 2),
        sha256_match=sha256_match,
        global_similarity=None if global_sim is None else round(global_sim, 2),
        keyframes_exact_pct=round(kf_exact_pct, 2),
        keyframes_close_pct=round(kf_close_pct, 2),
        duration_match=duration_match,
        duration_diff_pct=round(duration_diff_pct, 2),
    )


def match_level(
    sha256_match: bool,
    global_sim: float | None,
    kf_close_pct: float,
    duration_match: bool = True,
) -> tuple[str, float]:
    """Return (level, confidence %). Without global similarity, keyframes decide alone."""
    if sha256_match:
        return "EXACT", 100.0
    if not duration_match:
        return "NONE", 0.0

    if global_sim is None:
        if kf_close_pct >= 95.0:
            return "VERY_HIGH", min(99.0, kf_close_pct)
        if kf_close_pct >= 85.0:
            return "HIGH", kf_close_pct
        if kf_close_pct >= 50.0:
            return "MEDIUM", kf_close_pct
        if kf_close_pct >= 30.0:
            return "LOW", kf_close_pct
        return "NONE", 0.0

    if global_sim >= 95.0 and kf_close_pct >= 85.0:
        return "VERY_HIGH", min(99.0, (global_sim + kf_close_pct) / 2)
    if global_sim >= 85.0 and kf_close_pct >= 70.0:
        return "HIGH", (global_sim + kf_close_pct) / 2
    if kf_close_pct >= 50.0 or global_sim >= 75.0:
        return "MEDIUM", max(global_sim, kf_close_pct)
    if kf_close_pct >= 30.0:
        return "LOW", kf_close_pct
    return "NONE", 0.0


def _keyframe_scores(kf1: list[dict[str, Any]], kf2: list[dict[str, Any]]) -> tuple[float, float]:
    by_pos1 = {float(kf["position_pct"]): kf for kf in kf1}
    by_pos2 = {float(kf["position_pct"]): kf for kf in kf2}
    common = by_pos1.keys() & by_pos2.keys()
    if not common:
        return 0.0, 0.0
    exact = close = 0
    for pos in common:
        distance = hamming_distance(by_pos1[pos].get("phash"), by_pos2[pos].get("phash"))
        if distance is None:
            continue
        exact += distance <= KEYFRAME_EXACT_DISTANCE
        close += distance <= KEYFRAME_CLOSE_DISTANCE
    return exact / len(common) * 100, close / len(common) * 100


def _similarity(hex1: str | None, hex2: str | None, bits: int) -> float:
    distance = hamming_distance(hex1, hex2)
    return 0.0 if distance is None else (1 - distance / bits) * 100


def _major(version: Any) -> str:
    return str(version or "1.0").split(".", 1)[0]
