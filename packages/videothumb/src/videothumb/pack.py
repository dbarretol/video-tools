"""Pack: the contact sheet and the frames of a video in one ZIP file.

For ``clip.mp4``, ``clip_pack.zip`` holds::

    clip_sheet.jpg
    frames/clip_f001_00h00m05s.jpg
    frames/...

By default there is one frame per cell of the sheet, so the video is decoded
once: each full-size frame is written to the ZIP as it is decoded, and a
reduced copy fills its cell. With another ``count`` the sheet is sampled
separately. JPEGs are stored without compression, since deflate would not make
them smaller. The ZIP is written under a temporary name and renamed when
complete (see ``videocore.atomic_write``).
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from videocore import (
    NoFramesError,
    NoVideoStreamError,
    atomic_write,
    evenly_spaced,
    iter_frames,
    probe,
    sample_frames,
)
from videocore.options import option, validate
from videocore.verify import FrameCount, OutputError
from videothumb.common import Sampling, jpeg_quality_option, sampling_option
from videothumb.sheet import cell_size, render_sheet
from videothumb.snapshots import frame_name

logger = logging.getLogger(__name__)

SUFFIX = "_pack.zip"
FRAMES_FOLDER = "frames"


@dataclass(frozen=True)
class PackOptions:
    columns: int = option(5, "thumbnails per row", gt=0, group="contact sheet")
    rows: int = option(4, "rows of thumbnails", gt=0, group="contact sheet")
    thumbnail_width: int = option(
        320, "thumbnail width in pixels", ge=16, label="Thumbnail width", group="contact sheet"
    )
    count: int | None = option(
        None,
        "frames to save; another number than columns x rows decodes the video twice",
        gt=0,
        label="Frames",
        group="frames",
        default_text="columns x rows",
    )
    jpeg_quality: int = jpeg_quality_option()
    sampling: Sampling = sampling_option()

    def __post_init__(self) -> None:
        validate(self)

    @property
    def cells(self) -> int:
        return self.columns * self.rows

    @property
    def frame_count(self) -> int:
        return self.count or self.cells


@dataclass
class PackCounts:
    """How many frames could be decoded, for the sheet and for ``frames/``."""

    sheet: FrameCount
    frames: FrameCount


def default_pack_path(video: Path) -> Path:
    return video.with_name(f"{video.stem}{SUFFIX}")


def make_pack(
    video: str | Path,
    output: str | Path,
    options: PackOptions | None = None,
    *,
    decoders: int | None = None,
    counts: PackCounts | None = None,
    name: str | None = None,
) -> Path:
    """Write the ZIP of `video` at `output`; `name` starts every file name inside it.

    `decoders` limits the containers decoding in parallel (see ``videocore.iter_frames``);
    `counts`, when given, receives how many frames could be decoded.
    """
    options = options or PackOptions()
    info = probe(video)
    if info.video is None:
        raise NoVideoStreamError(f"No video stream in {info.path}")
    name = name or Path(video).stem
    size = cell_size(info, options.thumbnail_width)
    sheet_times = evenly_spaced(0, info.duration, options.cells, endpoints=False)
    shared = options.frame_count == options.cells
    times = sheet_times
    if not shared:
        times = evenly_spaced(0, info.duration, options.frame_count, endpoints=False)
    pad = max(3, len(str(len(times))))
    cells: list[Image.Image | None] = [None] * options.cells
    saved = 0

    output = Path(output)
    with atomic_write(output) as part, zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as archive:
        frames = iter_frames(
            info.path,
            times,
            mode=options.sampling,
            workers=decoders,
            stage="frames",
            fallback=True,
        )
        for index, image in frames:
            if image is None:
                continue
            seconds = image.info.get("time", times[index])
            entry = f"{FRAMES_FOLDER}/{frame_name(name, index + 1, seconds, pad)}"
            archive.writestr(entry, _jpeg(image, options.jpeg_quality))
            saved += 1
            if shared:
                cell = image.resize(size, Image.Resampling.BILINEAR)
                cell.info["time"] = seconds
                cells[index] = cell
        if saved == 0:
            raise NoFramesError(f"No frame could be decoded from {info.path}")
        if not shared:
            cells = sample_frames(
                info.path,
                sheet_times,
                mode=options.sampling,
                size=size,
                workers=decoders,
                stage="sheet",
                fallback=True,
            )
        sheet = render_sheet(info, cells, sheet_times, options.columns, size)
        archive.writestr(f"{name}_sheet.jpg", _jpeg(sheet, options.jpeg_quality))

    decoded = sum(cell is not None for cell in cells)
    if counts is not None:
        counts.frames.decoded, counts.frames.total = saved, len(times)
        counts.sheet.decoded, counts.sheet.total = decoded, len(cells)
    if saved < len(times) or decoded < len(cells):
        logger.warning(
            "Pack of %s has %d/%d frames and %d/%d sheet cells; the rest could not be decoded",
            info.path,
            saved,
            len(times),
            decoded,
            len(cells),
        )
    return output


def check_pack(path: Path, name: str, options: PackOptions, frames: int) -> None:
    """`path` is a readable ZIP with the sheet, of the expected width, and `frames` frames.

    Raises ``videocore.verify.OutputError`` otherwise.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            if (broken := archive.testzip()) is not None:
                raise OutputError(f"{path.name}: {broken} is damaged")
            names = archive.namelist()
            sheet = f"{name}_sheet.jpg"
            if sheet not in names:
                raise OutputError(f"{path.name} has no {sheet}")
            with archive.open(sheet) as file, Image.open(file) as image:
                width = image.width
                image.verify()
    except (OSError, SyntaxError, zipfile.BadZipFile) as exc:
        raise OutputError(f"{path.name} is not a readable ZIP: {exc}") from exc
    expected = options.columns * options.thumbnail_width
    if width != expected:
        raise OutputError(f"{sheet} is {width} pixels wide, expected {expected}")
    found = sum(entry.startswith(f"{FRAMES_FOLDER}/") for entry in names)
    if found != frames:
        raise OutputError(f"{path.name} has {found} frames, expected {frames}")


def _jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()
