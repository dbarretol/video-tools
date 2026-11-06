"""Archive manifest: the record that a video was archived completely.

``<name>.archive.json`` is written last in each output folder. It lists the
source video and every artifact with its size and SHA-256, and sets
``complete: true`` only when every requested artifact was written and read back
complete; incomplete ones are listed under ``problems``. A run that
fails writes it with ``complete: false`` and the error; a run that is killed
leaves no manifest at all. Either way the folder does not count as archived,
however complete it looks.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from videocore import VideoInfo, atomic_write
from videosemanticreducer import __version__
from videosemanticreducer.fingerprint import compute_sha256

MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".archive.json"


def manifest_path(output_dir: Path, name: str) -> Path:
    return output_dir / f"{name}{MANIFEST_SUFFIX}"


@dataclass(frozen=True)
class Source:
    path: Path
    size_bytes: int
    duration_seconds: float | None = None
    sha256: str | None = None
    """Only known when the fingerprint was computed."""

    @classmethod
    def from_info(cls, info: VideoInfo, sha256: str | None = None) -> Source:
        return cls(info.path, info.size_bytes, round(info.duration, 3), sha256)

    @classmethod
    def from_path(cls, path: Path) -> Source:
        """For a video that could not be probed."""
        return cls(path, path.stat().st_size if path.is_file() else 0)


def build_manifest(
    source: Source,
    options: dict[str, Any],
    outputs: Iterable[Path],
    *,
    error: str | None = None,
    problems: Iterable[str] = (),
) -> dict[str, Any]:
    """Describe an archive run; it is complete only without an error or problems."""
    problems = list(problems)
    return {
        "manifest_version": MANIFEST_VERSION,
        "tool": "videosemanticreducer",
        "tool_version": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {
            "filename": source.path.name,
            "path": str(source.path.resolve()),
            "size_bytes": source.size_bytes,
            "duration_seconds": source.duration_seconds,
            "sha256": source.sha256,
        },
        "options": options,
        "outputs": [
            {"file": path.name, "size_bytes": path.stat().st_size, "sha256": compute_sha256(path)}
            for path in outputs
        ],
        "complete": error is None and not problems,
        "error": error,
        "problems": problems,
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> Path:
    with atomic_write(path) as part:
        part.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_manifest(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """The manifest at `path`, or None when it is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_complete(path: str | os.PathLike[str]) -> bool:
    """True when the manifest says complete and every listed output is still there.

    Sizes are compared but hashes are not, so this is cheap enough to run over
    a whole library; the hashes are there for a full check before deleting originals.
    """
    path = Path(path)
    manifest = read_manifest(path)
    if manifest is None or manifest.get("complete") is not True:
        return False
    for output in manifest.get("outputs") or []:
        try:
            if (path.parent / output["file"]).stat().st_size != output["size_bytes"]:
                return False
        except (OSError, KeyError, TypeError):
            return False
    return True
