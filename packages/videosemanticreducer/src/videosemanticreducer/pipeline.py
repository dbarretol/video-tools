"""Process one video into its archive artifacts.

For ``movie.mp4`` the output folder gets:

- ``movie_info.txt``: metadata report.
- ``movie_summary.mp4``: info card followed by evenly spaced snapshots.
- ``movie_sheet.jpg``: contact sheet, a grid of frames (``movie_thumb.jpg`` in
  archives made before 0.2, which are still read).
- ``movie.fingerprint.json``: perceptual fingerprint.
- ``movie.archive.json``: manifest listing the above, written last; it says
  ``complete: true`` only when every requested artifact was written.

The video is decoded as few times as possible: the snapshots feed both the
summary video and the contact sheet, and the file hash runs in a background
thread while frames are decoded.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from videocore import (
    Footage,
    NoFramesError,
    NoVideoStreamError,
    OutputSlot,
    Status,
    VideoInfo,
    atomic_write,
    compose_grid,
    evenly_spaced,
    iter_frames,
    probe,
    progress,
    remove_partials,
    sample_frames,
)
from videocore.frames import FAR_KEYFRAME_SECONDS
from videocore.options import option, options_of, validate
from videocore.verify import FrameCount, check_file, check_image, check_video
from videosemanticreducer import fingerprint as fp
from videosemanticreducer.info_card import render_info_card
from videosemanticreducer.manifest import (
    MANIFEST_SUFFIX,
    Source,
    build_manifest,
    is_complete,
    manifest_path,
    read_manifest,
    write_manifest,
)
from videosemanticreducer.report import write_report
from videosemanticreducer.summary import SummaryWriter, even_size

logger = logging.getLogger(__name__)

SNAPSHOT_MAX_SIDE = 1280
THUMB_MAX_SIDE = 240
THUMB_JPEG_QUALITY = 80
INFO_CARD_DURATION = 8.0
OUTPUT_DIR_SUFFIX = "_summary"
SHEET_SUFFIX = "_sheet.jpg"
OLD_SHEET_SUFFIX = "_thumb.jpg"
"""The name of the contact sheet before 0.2."""
OLD_OPTION_NAMES = {
    "thumbnail": "sheet",
    "thumb_cols": "sheet_columns",
    "thumb_rows": "sheet_rows",
}
"""Option names of manifests written before 0.2."""


@dataclass(frozen=True)
class ProcessOptions:
    """What to produce for each video. Each option is also a flag and a config key."""

    num_snapshots: int = option(
        150,
        "snapshots in the summary video",
        flags=("-n", "--num-snapshots"),
        gt=0,
        label="Snapshots",
        group="summary video",
    )
    snap_duration: float = option(
        0.4,
        "seconds each snapshot is shown in the summary",
        flags=("-d", "--snap-duration"),
        gt=0,
        label="Seconds per snapshot",
        group="summary video",
    )
    # On the command line both are set with --grid CxR.
    sheet_columns: int = option(
        12, "contact sheet columns", gt=0, label="Columns", group="contact sheet", cli=False
    )
    sheet_rows: int = option(
        12, "contact sheet rows", gt=0, label="Rows", group="contact sheet", cli=False
    )
    summary: bool = option(
        True, "create <name>_summary.mp4", label="Summary video", group="artifacts"
    )
    sheet: bool = option(True, "create <name>_sheet.jpg", label="Contact sheet", group="artifacts")
    fingerprint: bool = option(
        True, "create <name>.fingerprint.json", label="Fingerprint", group="artifacts"
    )

    def __post_init__(self) -> None:
        validate(self)


@dataclass
class ProcessResult:
    video: Path
    output_dir: Path
    outputs: list[Path] = field(default_factory=list)
    """Artifacts written, in order; the manifest is not one of them."""
    manifest: Path | None = None
    timings: dict[str, float] = field(default_factory=dict)
    """Seconds spent in each step, for logging and benchmarks."""
    frames: dict[str, FrameCount] = field(default_factory=dict)
    """Frames decoded for the summary video and the contact sheet."""
    problems: list[str] = field(default_factory=list)
    """Why some artifacts are incomplete; such an archive is DEGRADED, not complete."""
    sheet_size: tuple[int, int] | None = None
    """Expected size of the contact sheet."""


def resolve_output_dir(video: Path, output: Path | None, *, batch: bool) -> Path:
    """Where the artifacts of `video` go.

    - no output given: ``<video folder>/<stem>_summary/``
    - output for a single video: ``<output>/``
    - output for a batch: ``<output>/<stem>_summary/``
    """
    if output is None:
        return video.parent / f"{video.stem}{OUTPUT_DIR_SUFFIX}"
    return output / f"{video.stem}{OUTPUT_DIR_SUFFIX}" if batch else output


def is_output_dir(path: Path) -> bool:
    """True for a ``<stem>_summary`` folder written by this tool.

    Recursive scans skip these folders, so the summary videos inside them are
    never archived again. A folder that only happens to end in ``_summary`` is
    still scanned unless it holds this tool's manifest, report or fingerprint
    (folders written before manifests existed have no manifest).
    """
    if not path.name.endswith(OUTPUT_DIR_SUFFIX):
        return False
    stem = path.name.removesuffix(OUTPUT_DIR_SUFFIX)
    markers = (f"{stem}{MANIFEST_SUFFIX}", f"{stem}_info.txt", f"{stem}.fingerprint.json")
    return any((path / marker).is_file() for marker in markers)


def snapshot_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spaced between 1% and 99% of the duration, skipping intros and credits."""
    return evenly_spaced(duration * 0.01, duration * 0.99, count)


def process_video(
    video: str | Path,
    output_dir: str | Path,
    options: ProcessOptions | None = None,
    *,
    name: str | None = None,
    decoders: int | None = None,
) -> ProcessResult:
    """Write the artifacts of `video` into `output_dir`, then its manifest.

    Artifacts are named after `name`, the video's stem by default; batches pass
    the full file name for videos whose stems collide (see ``videocore.outputs``).
    `decoders` limits the containers decoding in parallel; batches pass 1 since
    videos already run in parallel.
    Every artifact is read back once written (see `verify_artifacts`). The
    manifest (``<name>.archive.json``) is written last, with ``complete: true``
    only when every requested artifact was written and none is incomplete.
    """
    options = options or ProcessOptions()
    video, output_dir = Path(video), Path(output_dir)
    result = ProcessResult(video=video, output_dir=output_dir)
    timer = _Timer(result.timings)
    started = time.perf_counter()

    with timer("probe"):
        info = probe(video)
    if not info.has_video:
        # Nothing could prove later that a copy matches this file, so it is not archived.
        raise NoVideoStreamError(f"No video stream in {video}")
    stem = name or video.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    remove_partials(output_dir)  # left by a run that was killed
    result.manifest = manifest_path(output_dir, stem)
    # A rerun must not leave the previous "complete" record next to new, unfinished files.
    result.manifest.unlink(missing_ok=True)
    logger.info("Processing %s (%s, %s)", video, info.duration_hms, info.format_name)

    recorded = _recorded_options(options)
    try:
        sha256 = _write_artifacts(info, output_dir, stem, options, result, timer, decoders)
        with timer("verify"):
            result.problems = verify_artifacts(result, options)
    except Exception as exc:
        manifest = build_manifest(
            Source.from_info(info), recorded, result.outputs, error=f"{type(exc).__name__}: {exc}"
        )
        try:
            write_manifest(manifest, result.manifest)
        except OSError:
            logger.warning("Could not write %s", result.manifest, exc_info=True)
        raise
    with timer("manifest"):
        manifest = build_manifest(
            Source.from_info(info, sha256), recorded, result.outputs, problems=result.problems
        )
        write_manifest(manifest, result.manifest)

    result.timings["total"] = time.perf_counter() - started
    logger.info("Finished %s in %.1f s -> %s", video, result.timings["total"], output_dir)
    return result


def _write_artifacts(
    info: VideoInfo,
    output_dir: Path,
    stem: str,
    options: ProcessOptions,
    result: ProcessResult,
    timer: _Timer,
    decoders: int | None,
) -> str | None:
    """Write the requested artifacts into `result.outputs`; return the file's SHA-256."""
    result.outputs.append(write_report(info, output_dir / f"{stem}_info.txt"))
    with ThreadPoolExecutor(max_workers=1) as hasher:
        sha_future = hasher.submit(fp.compute_sha256, info.path) if options.fingerprint else None

        if options.summary or options.sheet:
            with timer("snapshots"):
                summary_path = output_dir / f"{stem}_summary.mp4"
                thumbs = _snapshots_and_thumbs(info, summary_path, options, result, decoders)
            if options.sheet:
                with timer("sheet"):
                    result.outputs.append(
                        _write_sheet(info, thumbs, output_dir / f"{stem}{SHEET_SUFFIX}", options)
                    )
                    result.frames["sheet"] = FrameCount(
                        sum(thumb is not None for thumb in thumbs), len(thumbs)
                    )
                    cell_w, cell_h = even_size(info.video.width, info.video.height, THUMB_MAX_SIDE)
                    result.sheet_size = (
                        options.sheet_columns * cell_w,
                        options.sheet_rows * cell_h,
                    )
                # A sheet under its old name would be left next to the new one, unlisted.
                old = output_dir / f"{stem}{OLD_SHEET_SUFFIX}"
                if old.is_file():
                    logger.info("Removing %s, replaced by %s%s", old, stem, SHEET_SUFFIX)
                    old.unlink()

        if sha_future is None:
            return None
        with timer("global_hashes"):
            global_hashes = fp.compute_global_hashes(info, workers=decoders)
        with timer("keyframe_hashes"):
            keyframe_hashes = fp.compute_keyframe_hashes(info, workers=decoders)
        with timer("sha256_wait"):
            sha256 = sha_future.result()
        fingerprint = fp.build_fingerprint(info, sha256, global_hashes, keyframe_hashes)
        # An incomplete fingerprint is never written: it would look like a valid archive.
        fp.check_complete(fingerprint)
        path = output_dir / f"{stem}.fingerprint.json"
        result.outputs.append(fp.save_fingerprint(fingerprint, path))
        return sha256


def verify_artifacts(result: ProcessResult, options: ProcessOptions) -> list[str]:
    """Read back every artifact of `result`; return why some are incomplete.

    Raises ``videocore.verify.OutputError`` when one is missing or unreadable.
    """
    problems = []
    for path in result.outputs:
        if path.name.endswith("_summary.mp4"):
            count = result.frames["summary"]
            expected = INFO_CARD_DURATION + count.decoded * options.snap_duration
            check_video(path, duration=expected)
        elif path.name.endswith((SHEET_SUFFIX, OLD_SHEET_SUFFIX)):
            check_image(path, size=result.sheet_size)
        elif path.name.endswith(".fingerprint.json"):
            fp.check_complete(fp.load_fingerprint(path))
        else:
            check_file(path)
    for what, count in result.frames.items():
        label = "summary video" if what == "summary" else "contact sheet"
        if problem := count.problem(label):
            problems.append(problem)
    return problems


def _recorded_options(options: ProcessOptions) -> dict[str, object]:
    """The options that shape the artifacts, by name."""
    return {opt.name: getattr(options, opt.name) for opt in options_of(ProcessOptions)}


ARCHIVE_COMPLETE = "complete"
ARCHIVE_OUTDATED = "outdated"
_ARTIFACT_TOGGLES = ("summary", "sheet", "fingerprint")


def recorded_options(manifest: dict | None) -> dict[str, object]:
    """The options a manifest was made with, by their current names."""
    recorded = dict((manifest or {}).get("options") or {})
    for old, new in OLD_OPTION_NAMES.items():
        if old in recorded:
            recorded.setdefault(new, recorded.pop(old))
    return recorded


def archive_state(
    output_dir: Path, name: str, options: ProcessOptions, *, video: Path | None = None
) -> str | None:
    """Whether the video named `name` still needs archiving with `options`.

    - ``"complete"``: a complete archive made with the same options.
    - ``"outdated"``: complete and with every requested artifact, but made with
      other settings (e.g. another number of snapshots). It is not redone unless
      the user asks, since that can mean re-archiving a whole library.
    - None: it must be archived, because there is no complete manifest or an
      artifact requested now (say, the fingerprint) was not made back then,
      or the archive is of another file: `video` (when given) does not have the
      size recorded, as when a video was replaced by another of the same name.
    """
    path = manifest_path(output_dir, name)
    if not is_complete(path):
        return None
    manifest = read_manifest(path)
    if video is not None and not _same_size(video, manifest):
        return None
    recorded = recorded_options(manifest)
    wanted = _recorded_options(options)
    if any(wanted[toggle] and not recorded.get(toggle) for toggle in _ARTIFACT_TOGGLES):
        return None
    settings = [key for key in wanted if key not in _ARTIFACT_TOGGLES]
    same = all(recorded.get(key) == wanted[key] for key in settings)
    return ARCHIVE_COMPLETE if same else ARCHIVE_OUTDATED


def _same_size(video: Path, manifest: dict | None) -> bool:
    recorded = ((manifest or {}).get("source") or {}).get("size_bytes")
    try:
        return recorded is None or video.stat().st_size == recorded
    except OSError:
        return True  # a missing video fails later with a clear error


def _slot_dir(slot: OutputSlot) -> Path:
    return slot.folder / f"{slot.name}{OUTPUT_DIR_SUFFIX}"


# Single-worker cost model of an archive, fitted on 38 real videos (720p to 1080p,
# 24 to 60 fps, keyframes every 2 to 36 s, 5 s to 66 min): 87% within 25% of the
# measured time. Each run also corrects it for the computer (see `Plan.speed`).
ARCHIVE_BASE_SECONDS = 3.9
"""Hashes, SHA-256, info card, files: what does not grow with the snapshots."""
ARCHIVE_SECONDS_PER_MEGAPIXEL = 3.8
"""Per megapixel of frame for the default 150 snapshots: one keyframe each, plus encoding."""
ARCHIVE_SECONDS_PER_DECODED_GIGAPIXEL = 0.82
"""Decoding frames one after another, for the snapshots that need precise sampling."""
DEFAULT_KEYFRAME_INTERVAL = 4.0
"""Assumed when it could not be read: common in screen recordings and downloads."""
UNKNOWN_FOOTAGE = (1280, 720, 30.0)
"""Width, height and frame rate assumed when they are unknown."""
ARCHIVE_SPEED_KEY = "archive.2"
"""Where the speed of this computer is learned (see ``videocore.State.speed``).
Changed with the cost model, so a speed learned against the old one is not reused."""


@dataclass(frozen=True)
class ArchiveTask:
    """``videocore.jobs.Task`` that archives each video into its ``<name>_summary`` folder."""

    name: str = "archive"

    def output(self, slot: OutputSlot) -> Path:
        return _slot_dir(slot)

    def state(self, video: Path, output: Path, name: str, options: ProcessOptions) -> Status | None:
        state = archive_state(output, name, options, video=video)
        if state == ARCHIVE_COMPLETE:
            return Status.SKIPPED
        return Status.OUTDATED if state == ARCHIVE_OUTDATED else None

    def run(
        self, video: Path, output: Path, name: str, options: ProcessOptions, decoders: int | None
    ) -> ProcessResult:
        return process_video(video, output, options, name=name, decoders=decoders)

    def verify(self, output: Path, value: ProcessResult, options: ProcessOptions) -> list[str]:
        return value.problems  # checked before the manifest was written

    def cleanup(self, output: Path) -> None:
        remove_partials(output)

    def estimate(self, footage: Footage, options: ProcessOptions) -> float | None:
        return estimate_archive(footage, options)


def estimate_archive(footage: Footage, options: ProcessOptions) -> float:
    """Rough seconds to archive a video like `footage` with one worker.

    Snapshots come from keyframes, cheap one by one; those that would repeat a
    keyframe (keyframes further apart than the snapshots) or fall far from it
    are decoded precisely, which means decoding the frames in between: that
    part grows with the pixels per second and the keyframe interval, not with
    the length of the video beyond about `snapshots` x interval.
    """
    width, height, fps = (
        (footage.width, footage.height, footage.fps) if footage.pixel_rate else UNKNOWN_FOOTAGE
    )
    interval = footage.keyframe_interval or DEFAULT_KEYFRAME_INTERVAL
    count = options.num_snapshots if options.summary else options.sheet_columns * options.sheet_rows
    duration = max(footage.duration, 0.0)
    spacing = duration / count
    repeated = max(0.0, 1 - spacing / interval)
    far = max(0.0, (interval - FAR_KEYFRAME_SECONDS) / interval)
    decoded_seconds = min(1.0, repeated + far) * min(duration, count * interval / 2)
    megapixels = width * height / 1e6
    return (
        ARCHIVE_BASE_SECONDS
        + ARCHIVE_SECONDS_PER_MEGAPIXEL * megapixels * count / 150
        + ARCHIVE_SECONDS_PER_DECODED_GIGAPIXEL * megapixels * fps * decoded_seconds / 1000
    )


ARCHIVE = ArchiveTask()


def _snapshots_and_thumbs(
    info: VideoInfo,
    summary_path: Path,
    options: ProcessOptions,
    result: ProcessResult,
    decoders: int | None,
) -> list[Image.Image | None]:
    """Decode the snapshots once: encode the summary and keep small copies for the grid.

    Returns the cells of the contact sheet (empty when no sheet was requested).
    """
    assert info.video is not None
    cells = options.sheet_columns * options.sheet_rows
    thumb_size = even_size(info.video.width, info.video.height, THUMB_MAX_SIDE)
    if not options.summary:
        timestamps = snapshot_timestamps(info.duration, cells)
        return sample_frames(
            info.path,
            timestamps,
            size=thumb_size,
            workers=decoders,
            stage="sheet",
            fallback=True,
        )

    snap_size = even_size(info.video.width, info.video.height, SNAPSHOT_MAX_SIDE)
    timestamps = snapshot_timestamps(info.duration, options.num_snapshots)
    # With enough snapshots, grid cell i shows the snapshot nearest to its own evenly
    # spaced position; otherwise the grid is sampled separately afterwards.
    reuse_for_grid = options.sheet and options.num_snapshots >= cells
    grid_sources = _grid_sources(options.num_snapshots, cells) if reuse_for_grid else {}
    thumbs: list[Image.Image | None] = [None] * cells if reuse_for_grid else []
    decoded = 0

    with SummaryWriter(summary_path, snap_size, still_duration=options.snap_duration) as writer:
        writer.add(render_info_card(info, snap_size), duration=INFO_CARD_DURATION)
        # Frames arrive in order while the next ones are decoded, so encoding overlaps
        # decoding and only a few full-size frames are in memory at a time.
        frames = iter_frames(
            info.path,
            timestamps,
            size=snap_size,
            workers=decoders,
            stage="snapshots",
            fallback=True,
        )
        for index, image in frames:
            if image is None:
                continue
            decoded += 1
            writer.add(image)
            for cell in grid_sources.get(index, ()):
                thumbs[cell] = image.resize(thumb_size, Image.Resampling.BILINEAR)
        if decoded == 0:
            raise NoFramesError(f"No snapshot could be decoded from {info.path}")

    result.frames["summary"] = FrameCount(decoded, len(timestamps))
    if decoded < len(timestamps):
        logger.warning("Decoded %d/%d snapshots of %s", decoded, len(timestamps), info.path)
    result.outputs.append(summary_path)

    if options.sheet and not reuse_for_grid:
        thumbs = sample_frames(
            info.path,
            snapshot_timestamps(info.duration, cells),
            size=thumb_size,
            workers=decoders,
            stage="sheet",
            fallback=True,
        )
    return thumbs


def _grid_sources(snapshots: int, cells: int) -> dict[int, list[int]]:
    """Map snapshot index -> grid cells it fills (nearest evenly spaced position)."""
    sources: dict[int, list[int]] = {}
    for cell in range(cells):
        index = 0 if cells == 1 else round(cell * (snapshots - 1) / (cells - 1))
        sources.setdefault(index, []).append(cell)
    return sources


def _write_sheet(
    info: VideoInfo, thumbs: list[Image.Image | None], path: Path, options: ProcessOptions
) -> Path:
    decoded = sum(thumb is not None for thumb in thumbs)
    if decoded == 0:
        raise NoFramesError(f"No frame of the contact sheet could be decoded from {info.path}")
    if decoded < len(thumbs):
        logger.warning(
            "Contact sheet of %s has %d/%d cells; the rest are black",
            info.path,
            decoded,
            len(thumbs),
        )
    assert info.video is not None
    cell_size = even_size(info.video.width, info.video.height, THUMB_MAX_SIDE)
    grid = compose_grid(thumbs, options.sheet_columns, cell_size=cell_size)
    with atomic_write(path) as part:
        grid.save(part, "JPEG", quality=THUMB_JPEG_QUALITY)
    return path


class _Timer:
    def __init__(self, timings: dict[str, float]) -> None:
        self.timings = timings

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        if not name.endswith("_wait"):
            progress.stage(name)  # stages that decode frames then report each one
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + time.perf_counter() - start
