"""Title card shown at the start of the summary video."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from videocore import VideoInfo
from videosemanticreducer.report import format_size

BACKGROUND = (0x1A, 0x1A, 0x2E)
TITLE_COLOR = (255, 255, 0)
TEXT_COLOR = (255, 255, 255)
RELEVANT_TAGS = ("title", "artist", "album", "comment", "description")

# Layout in pixels for a 1280x720 card; scaled to the actual card size.
REFERENCE_SIZE = (1280, 720)
TITLE_SIZE, TITLE_Y = 36, 40
BODY_SIZE, BODY_X, BODY_Y, LINE_SPACING = 24, 60, 120, 40


def card_lines(info: VideoInfo) -> list[str]:
    lines = [
        f"Size: {format_size(info.size_bytes)}",
        f"Duration: {info.duration_hms}",
    ]
    if info.video is not None:
        lines += [f"Resolution: {info.video.resolution}", f"Frame rate: {info.video.fps:.2f} fps"]
    codecs = info.video.codec if info.video is not None else "n/a"
    if info.audio is not None:
        codecs += f" / {info.audio.codec}"
    lines += [f"Codecs: {codecs}", f"Format: {info.format_name}"]
    if info.bit_rate:
        lines.append(f"Bit rate: {info.bit_rate // 1000} kb/s")

    tags = {key.lower(): value for key, value in info.tags.items()}
    for key in RELEVANT_TAGS:
        if value := tags.get(key):
            lines.append(f"{key.capitalize()}: {_truncate(str(value), 80)}")
    return lines


def render_info_card(info: VideoInfo, size: tuple[int, int]) -> Image.Image:
    """Draw the file name and main metadata on a plain background."""
    width, height = size
    scale = min(width / REFERENCE_SIZE[0], height / REFERENCE_SIZE[1])
    card = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(card)

    title_font = ImageFont.load_default(size=max(10, round(TITLE_SIZE * scale)))
    body_font = ImageFont.load_default(size=max(8, round(BODY_SIZE * scale)))

    title = _truncate(info.filename, 60)
    title_width = draw.textlength(title, font=title_font)
    draw.text(
        ((width - title_width) / 2, TITLE_Y * scale), title, font=title_font, fill=TITLE_COLOR
    )
    for index, line in enumerate(card_lines(info)):
        y = (BODY_Y + index * LINE_SPACING) * scale
        draw.text((BODY_X * scale, y), line, font=body_font, fill=TEXT_COLOR)
    return card


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
