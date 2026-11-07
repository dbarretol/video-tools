"""Check archives on disk, long after they were made: can the originals be deleted?

`check_archive` reads the manifest of one archive and checks that every
artifact it lists is still there, unchanged (by size, or by SHA-256 with
``full``), and that the original video, if still present, is the one that was
archived. `find_archives` pairs the videos of a folder with their archives,
and also finds archives whose original is gone.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from videocore import RunOptions, find_videos, plan
from videosemanticreducer.fingerprint import compute_sha256
from videosemanticreducer.manifest import MANIFEST_SUFFIX, manifest_path, read_manifest
from videosemanticreducer.pipeline import (
    ARCHIVE,
    ProcessOptions,
    is_output_dir,
    resolve_output_dir,
)


class ArchiveStatus(StrEnum):
    READY = "ready"
    """Complete and unchanged: the original can be deleted."""
    INCOMPLETE = "incomplete"
    """Made, but some artifacts are incomplete (DEGRADED); archive it again."""
    FAILED = "failed"
    """The archive run failed or did not finish."""
    CHANGED = "changed"
    """Artifacts are missing or modified, or the original is not the video archived."""
    MISSING = "not archived"


@dataclass
class ArchiveCheck:
    folder: Path
    name: str
    video: Path | None
    """The original video; None when it is not known."""
    status: ArchiveStatus = ArchiveStatus.READY
    details: list[str] = field(default_factory=list)
    """Why it is not ready, and notes such as "the original is gone"."""

    @property
    def label(self) -> str:
        return self.video.name if self.video is not None else f"{self.folder.name}/{self.name}"


def check_archive(
    folder: Path, name: str, video: Path | None = None, *, full: bool = False
) -> ArchiveCheck:
    """Check the archive `name` in `folder`; `video` defaults to the path in its manifest."""
    check = ArchiveCheck(folder, name, video)
    manifest = read_manifest(manifest_path(folder, name))
    if manifest is None:
        check.status = ArchiveStatus.MISSING
        return check
    source = manifest.get("source") or {}
    if check.video is None and source.get("path"):
        check.video = Path(source["path"])

    if manifest.get("error"):
        check.status = ArchiveStatus.FAILED
        check.details.append(str(manifest["error"]))
        return check
    if problems := manifest.get("problems"):
        check.status = ArchiveStatus.INCOMPLETE
        check.details.extend(str(problem) for problem in problems)
        return check
    if manifest.get("complete") is not True:
        check.status = ArchiveStatus.FAILED
        check.details.append("the archive run did not finish")
        return check

    changed = [*_changed_outputs(folder, manifest.get("outputs") or [], full)]
    changed += _changed_source(check.video, source, full)
    if changed:
        check.status = ArchiveStatus.CHANGED
        check.details.extend(changed)
    if not any(str(o.get("file", "")).endswith(".fingerprint.json") for o in manifest["outputs"]):
        check.details.append("no fingerprint: a copy of the video cannot be verified later")
    return check


def _changed_outputs(folder: Path, outputs: Iterable[dict], full: bool) -> Iterable[str]:
    for output in outputs:
        try:
            path = folder / output["file"]
            size, sha256 = output["size_bytes"], output.get("sha256")
        except (KeyError, TypeError):
            yield "the manifest lists an artifact it does not describe"
            continue
        if not path.is_file():
            yield f"{path.name} is missing"
        elif path.stat().st_size != size or (full and sha256 and compute_sha256(path) != sha256):
            yield f"{path.name} was modified"


def _changed_source(video: Path | None, source: dict, full: bool) -> list[str]:
    if video is None:
        return []
    if not video.is_file():
        return []  # deleted already, which is what the archive is for
    if source.get("size_bytes") is not None and video.stat().st_size != source["size_bytes"]:
        return [f"{video.name} is not the video that was archived (another size)"]
    sha256 = source.get("sha256")
    if full and sha256 and compute_sha256(video) != sha256:
        return [f"{video.name} is not the video that was archived (another SHA-256)"]
    return []


def _note_missing_original(check: ArchiveCheck) -> None:
    if check.status is ArchiveStatus.MISSING or check.video is None:
        return
    if not check.video.exists():
        check.details.append(f"the original is no longer at {check.video}")


def find_archives(
    path: Path, *, output: Path | None = None, recursive: bool = False, full: bool = False
) -> list[ArchiveCheck]:
    """Check the archive of a video, every archive in an archive folder, or a whole folder.

    For a folder, each video is paired with its archive where ``archive`` would
    have put it (see `output`), and archive folders inside it whose original is
    gone are listed too.
    """
    if path.is_file():
        folder = resolve_output_dir(path, output, batch=False)
        checks = [check_archive(folder, path.stem, path, full=full)]
    elif is_output_dir(path):
        checks = [check_archive(path, name, full=full) for name in _manifest_names(path)]
    else:
        checks = _check_folder(path, output, recursive, full)
    for check in checks:
        _note_missing_original(check)
    return checks


def _check_folder(
    folder: Path, output: Path | None, recursive: bool, full: bool
) -> list[ArchiveCheck]:
    videos = find_videos(folder, recursive=recursive, skip_dir=is_output_dir)
    run = RunOptions(output=output, recursive=recursive)
    run = dataclasses.replace(run, overwrite=True)  # where outputs go, not whether they exist
    planned = plan(ARCHIVE, videos, ProcessOptions(), run, source=folder, measure=False)
    checks = [check_archive(i.output, i.name, i.video, full=full) for i in planned.items]
    known = {(check.folder.resolve(), check.name) for check in checks}
    # Archives whose original was deleted (or moved) since.
    pattern = "**/*" if recursive else "*"
    for root in dict.fromkeys(r for r in (folder, output) if r is not None and r.is_dir()):
        for archive in sorted(p for p in root.glob(pattern) if p.is_dir() and is_output_dir(p)):
            for name in _manifest_names(archive):
                if (archive.resolve(), name) not in known:
                    known.add((archive.resolve(), name))
                    checks.append(check_archive(archive, name, full=full))
    return checks


def _manifest_names(folder: Path) -> list[str]:
    return sorted(p.name.removesuffix(MANIFEST_SUFFIX) for p in folder.glob(f"*{MANIFEST_SUFFIX}"))
