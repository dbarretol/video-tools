"""Grid composition of same-sized images (contact sheets, collages)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PIL import Image


def compose_grid(
    images: Sequence[Image.Image | None],
    columns: int,
    *,
    cell_size: tuple[int, int] | None = None,
    header_height: int = 0,
    background: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Paste `images` row by row into a grid with `columns` columns.

    None entries leave their cell empty. `cell_size` defaults to the size of the
    first image; images of a different size are resized to fit. A blank band of
    `header_height` pixels is reserved at the top for the caller to draw on.
    """
    if columns <= 0:
        raise ValueError("columns must be greater than 0")
    if cell_size is None:
        first = next((img for img in images if img is not None), None)
        if first is None:
            raise ValueError("cell_size is required when there are no images")
        cell_size = first.size

    cell_w, cell_h = cell_size
    rows = max(1, math.ceil(len(images) / columns))
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h + header_height), background)

    for idx, img in enumerate(images):
        if img is None:
            continue
        if img.size != cell_size:
            img = img.resize(cell_size, Image.Resampling.LANCZOS)
        col, row = idx % columns, idx // columns
        sheet.paste(img, (col * cell_w, header_height + row * cell_h))
    return sheet
