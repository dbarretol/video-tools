"""Three-layer video fingerprint: file hash, global perceptual hashes, per-position hashes.

- ``sha256``: identifies the exact same file.
- ``global_hashes``: pHash/dHash/aHash/wHash (256 bits each) of a collage of
  frames spread over the whole video; survives re-encoding and resizing.
- ``keyframe_hashes``: pHash/dHash (64 bits) of single frames at fixed
  percentages of the duration; tolerates partial edits.

Format 2.0 takes every frame with precise seeking, so the hashes depend only on
the content and not on where the encoder placed keyframes. Format 1.0 (the
original tool) took the collage frames from keyframes only, so its global hashes
are not comparable with 2.0; its keyframe hashes are.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

import imagehash

from videocore import VideoInfo, compose_grid, evenly_spaced, sample_frames

logger = logging.getLogger(__name__)

FORMAT_VERSION = "2.0"
REQUIRED_FIELDS = ("version", "sha256", "global_hashes", "keyframe_hashes")

# Percentages of the duration where single frames are hashed (same as format 1.0).
KEY_POSITIONS = (0.5, 2, 5, *range(10, 100, 5), 98)
GLOBAL_FRAMES = 64
GLOBAL_FRAME_SIZE = 144
GLOBAL_HASH_SIZE = 16
KEYFRAME_SIZE = 256
KEYFRAME_HASH_SIZE = 8

SHA_CHUNK_SIZE = 8 * 1024 * 1024


def compute_sha256(path: str | os.PathLike[str]) -> str:
    """SHA-256 of the whole file. hashlib releases the GIL, so this can run in a thread."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(SHA_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def compute_global_hashes(
    info: VideoInfo, *, frames: int = GLOBAL_FRAMES, workers: int | None = None
) -> dict[str, Any] | None:
    """Hash a square collage of `frames` frames taken between 1% and 99% of the video."""
    timestamps = evenly_spaced(info.duration * 0.01, info.duration * 0.99, frames)
    size = (GLOBAL_FRAME_SIZE, GLOBAL_FRAME_SIZE)
    images = sample_frames(info.path, timestamps, mode="precise", size=size, workers=workers)
    decoded = sum(img is not None for img in images)
    if decoded == 0:
        logger.warning("No frame decoded for the global hashes of %s", info.path)
        return None
    if decoded < frames:
        logger.warning("Global hashes of %s use %d/%d frames", info.path, decoded, frames)

    # Missing frames stay as black cells so the layout never shifts.
    collage = compose_grid(images, math.ceil(math.sqrt(frames)), cell_size=size)
    return {
        "phash_global": str(imagehash.phash(collage, hash_size=GLOBAL_HASH_SIZE)),
        "dhash_global": str(imagehash.dhash(collage, hash_size=GLOBAL_HASH_SIZE)),
        "ahash_global": str(imagehash.average_hash(collage, hash_size=GLOBAL_HASH_SIZE)),
        "whash_global": str(imagehash.whash(collage, hash_size=GLOBAL_HASH_SIZE)),
        "collage_frames": decoded,
        "frame_size": GLOBAL_FRAME_SIZE,
    }


def compute_keyframe_hashes(
    info: VideoInfo,
    *,
    positions: tuple[float, ...] = KEY_POSITIONS,
    workers: int | None = None,
) -> list[dict[str, Any]]:
    """Hash single frames at fixed percentages of the duration; skip positions that fail."""
    timestamps = [info.duration * pct / 100 for pct in positions]
    size = (KEYFRAME_SIZE, KEYFRAME_SIZE)
    images = sample_frames(info.path, timestamps, mode="precise", size=size, workers=workers)
    hashes = []
    for pct, ts, img in zip(positions, timestamps, images, strict=True):
        if img is None:
            logger.warning("No frame at %s%% (%.3f s) of %s", pct, ts, info.path)
            continue
        hashes.append(
            {
                "position_pct": pct,
                "timestamp": round(ts, 3),
                "phash": str(imagehash.phash(img, hash_size=KEYFRAME_HASH_SIZE)),
                "dhash": str(imagehash.dhash(img, hash_size=KEYFRAME_HASH_SIZE)),
            }
        )
    return hashes


def build_fingerprint(
    info: VideoInfo,
    sha256: str,
    global_hashes: dict[str, Any] | None,
    keyframe_hashes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": FORMAT_VERSION,
        "filename": info.filename,
        "file_size_bytes": info.size_bytes,
        "duration_seconds": round(info.duration, 3),
        "sha256": sha256,
        "global_hashes": global_hashes,
        "keyframe_hashes": keyframe_hashes,
    }


def compute_fingerprint(
    info: VideoInfo, *, sha256: str | None = None, workers: int | None = None
) -> dict[str, Any]:
    """Compute all layers. Pass `sha256` when it was already computed elsewhere."""
    if sha256 is None:
        sha256 = compute_sha256(info.path)
    global_hashes, keyframe_hashes = None, []
    if info.has_video:
        global_hashes = compute_global_hashes(info, workers=workers)
        keyframe_hashes = compute_keyframe_hashes(info, workers=workers)
    return build_fingerprint(info, sha256, global_hashes, keyframe_hashes)


def save_fingerprint(fingerprint: dict[str, Any], path: Path) -> Path:
    path.write_text(json.dumps(fingerprint, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_fingerprint(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a fingerprint file of any supported version; raise ValueError if it is invalid."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Fingerprint not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid fingerprint JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid fingerprint in {path}: expected a JSON object")
    missing = [name for name in REQUIRED_FIELDS if name not in data]
    if missing:
        raise ValueError(f"Invalid fingerprint in {path}: missing {', '.join(missing)}")
    return data
