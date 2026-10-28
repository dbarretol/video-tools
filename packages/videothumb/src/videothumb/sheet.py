"""Contact sheet: a grid of evenly spaced thumbnails under a metadata header.

Each thumbnail is labeled with the time of the frame it shows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from videocore import VideoInfo, compose_grid, evenly_spaced, format_hms, probe, sample_frames
from videothumb.common import Sampling

BACKGROUND = (30, 30, 30)
TEXT_COLOR = (255, 255, 255)
TIMESTAMP_COLOR = (255, 255, 0)
HEADER_HEIGHT = 80
TITLE_SIZE, DETAILS_SIZE, LABEL_SIZE = 22, 15, 14


@dataclass(frozen=True)
class SheetOptions:
    columns: int = 5
    rows: int = 4
    thumbnail_width: int = 320
    jpeg_quality: int = 90
    sampling: Sampling = "keyframe"
    workers: int | None = None
    """Containers decoding in parallel; use 1 when processing videos in parallel."""

    def __post_init__(self) -> None:
        if self.columns <= 0 or self.rows <= 0:
            raise ValueError("columns and rows must be greater than 0")
        if self.thumbnail_width < 16:
            raise ValueError("thumbnail_width must be at least 16")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")

    @property
    def count(self) -> int:
        return self.columns * self.rows


def default_sheet_path(video: Path) -> Path:
    return video.with_name(f"{video.stem}_sheet.jpg")


def make_sheet(video: str | Path, output: str | Path, options: SheetOptions | None = None) -> Path:
    """Build the contact sheet of `video` and save it as a JPEG at `output`."""
    options = options or SheetOptions()
    info = probe(video)
    if info.video is None:
        raise ValueError(f"No video stream in {info.path}")

    timestamps = evenly_spaced(0, info.duration, options.count, endpoints=False)
    width = options.thumbnail_width
    height = max(2, round(width * info.video.height / info.video.width))
    frames = sample_frames(
        info.path, timestamps, mode=options.sampling, size=(width, height), workers=options.workers
    )
    if all(frame is None for frame in frames):
        raise RuntimeError(f"No frame could be decoded from {info.path}")

    sheet = render_sheet(info, frames, timestamps, options.columns, (width, height))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "JPEG", quality=options.jpeg_quality)
    return output


def render_sheet(
    info: VideoInfo,
    frames: Sequence[Image.Image | None],
    timestamps: Sequence[float],
    columns: int,
    cell_size: tuple[int, int],
) -> Image.Image:
    long_video = info.duration >= 3600
    label_font = ImageFont.load_default(size=LABEL_SIZE)
    labeled = []
    for frame, requested in zip(frames, timestamps, strict=True):
        if frame is not None:
            # Label the frame's real time: in keyframe mode it can be earlier than requested.
            actual = frame.info.get("time")
            label = format_time(requested if actual is None else actual, long=long_video)
            frame = _draw_label(frame.copy(), label, label_font)
        labeled.append(frame)

    sheet = compose_grid(
        labeled, columns, cell_size=cell_size, header_height=HEADER_HEIGHT, background=BACKGROUND
    )
    _draw_header(sheet, info)
    return sheet


def format_time(seconds: float, *, long: bool = False) -> str:
    """``MM:SS``, or ``H:MM:SS`` when `long` (videos of an hour or more)."""
    if long:
        return format_hms(seconds)
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def header_lines(info: VideoInfo) -> list[str]:
    details = [f"Duration: {info.duration_hms}"]
    if info.video is not None:
        details += [f"Resolution: {info.video.resolution}", f"Codec: {info.video.codec}"]
    if info.bit_rate:
        details.append(f"Bit rate: {info.bit_rate // 1000} kb/s")
    return [info.filename, "  |  ".join(details)]


def _draw_header(sheet: Image.Image, info: VideoInfo) -> None:
    draw = ImageDraw.Draw(sheet)
    title, details = header_lines(info)
    draw.text((10, 12), title, fill=TEXT_COLOR, font=ImageFont.load_default(size=TITLE_SIZE))
    draw.text((10, 44), details, fill=TEXT_COLOR, font=ImageFont.load_default(size=DETAILS_SIZE))


def _draw_label(image: Image.Image, label: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    pad = 3
    box_w, box_h = right - left + 2 * pad, bottom - top + 2 * pad
    x0, y0 = image.width - box_w, image.height - box_h
    draw.rectangle((x0, y0, image.width, image.height), fill=(0, 0, 0))
    draw.text((x0 + pad - left, y0 + pad - top), label, fill=TIMESTAMP_COLOR, font=font)
    return image
