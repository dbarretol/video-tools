"""Three-layer video fingerprint: file hash, global perceptual hashes, per-position hashes.

- ``sha256``: identifies the exact same file.
- ``global_hashes``: pHash/dHash/aHash/wHash (256 bits each) of a collage of
  frames spread over the whole video; survives re-encoding and resizing.
- ``keyframe_hashes``: pHash/dHash (64 bits) of single frames at fixed
  percentages of the duration; tolerates partial edits.

Format 2.0 takes every frame with precise seeking, so the hashes depend only on
the content and not on where the encoder placed keyframes. Frames are decoded in
draft quality (see ``videocore.frames``): at hashing sizes it changes nothing and
it is much faster. Format 1.0 (the
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

from videocore import (
    NoVideoStreamError,
    VideoInfo,
    atomic_write,
    compose_grid,
    evenly_spaced,
    progress,
    sample_frames,
)

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
# A fingerprint that hashes fewer positions than this cannot confirm a match later.
MIN_KEYFRAME_RATIO = 0.5


class IncompleteFingerprintError(RuntimeError):
    """The video could not be hashed well enough to verify a copy against it later."""

    hint = (
        "too few frames could be decoded to recognize this video later, so it was not "
        "archived; the file may be damaged (check that it plays)"
    )


def compute_sha256(path: str | os.PathLike[str]) -> str:
    """SHA-256 of the whole file. hashlib releases the GIL, so this can run in a thread."""
    digest = hashlib.sha256()
    total, done = os.path.getsize(path), 0
    with open(path, "rb") as fh:
        while chunk := fh.read(SHA_CHUNK_SIZE):
            progress.check_cancelled()
            digest.update(chunk)
            done += len(chunk)
            progress.stage("sha256", done, total)
    return digest.hexdigest()


def compute_global_hashes(
    info: VideoInfo, *, frames: int = GLOBAL_FRAMES, workers: int | None = None
) -> dict[str, Any] | None:
    """Hash a square collage of `frames` frames taken between 1% and 99% of the video."""
    timestamps = evenly_spaced(info.duration * 0.01, info.duration * 0.99, frames)
    size = (GLOBAL_FRAME_SIZE, GLOBAL_FRAME_SIZE)
    images = sample_frames(
        info.path,
        timestamps,
        mode="precise",
        size=size,
        workers=workers,
        draft=True,
        stage="global_hashes",
    )
    decoded = sum(img is not None for img in images)
    if decoded == 0:
        logger.warning("No frame decoded for the global hashes of %s", info.path)
        return None
    if decoded < frames:
        logger.warning("Global hashes of %s use %d/%d frames", info.path, decoded, frames)

    import imagehash  # with numpy, slow to import: only when hashing

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
    images = sample_frames(
        info.path,
        timestamps,
        mode="precise",
        size=size,
        workers=workers,
        draft=True,
        stage="keyframe_hashes",
    )
    import imagehash  # with numpy, slow to import: only when hashing

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


def check_complete(fingerprint: dict[str, Any], *, positions: int = len(KEY_POSITIONS)) -> None:
    """Raise IncompleteFingerprintError unless every layer can back a later verification.

    A fingerprint is the proof that an archived video can be deleted, so a
    partial one is a failure rather than a warning: it needs the global hashes
    and at least `MIN_KEYFRAME_RATIO` of the keyframe positions.
    """
    name = fingerprint.get("filename")
    if not fingerprint.get("global_hashes"):
        raise IncompleteFingerprintError(f"No frame could be hashed for the fingerprint of {name}")
    hashed = len(fingerprint.get("keyframe_hashes") or [])
    if hashed < math.ceil(positions * MIN_KEYFRAME_RATIO):
        raise IncompleteFingerprintError(
            f"Only {hashed}/{positions} keyframe positions could be hashed for {name}"
        )


def compute_fingerprint(
    info: VideoInfo, *, sha256: str | None = None, workers: int | None = None
) -> dict[str, Any]:
    """Compute all layers. Pass `sha256` when it was already computed elsewhere.

    Raises ValueError for files without a video stream and IncompleteFingerprintError
    when too few frames could be decoded.
    """
    if not info.has_video:
        raise NoVideoStreamError(f"No video stream in {info.path}")
    if sha256 is None:
        sha256 = compute_sha256(info.path)
    global_hashes = compute_global_hashes(info, workers=workers)
    keyframe_hashes = compute_keyframe_hashes(info, workers=workers)
    fingerprint = build_fingerprint(info, sha256, global_hashes, keyframe_hashes)
    check_complete(fingerprint)
    return fingerprint


def save_fingerprint(fingerprint: dict[str, Any], path: Path) -> Path:
    with atomic_write(path) as part:
        part.write_text(json.dumps(fingerprint, indent=2, ensure_ascii=False), encoding="utf-8")
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
