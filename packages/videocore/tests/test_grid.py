import pytest
from PIL import Image

from videocore import compose_grid

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def solid(color, size=(10, 8)):
    return Image.new("RGB", size, color)


def test_layout_rows_and_header():
    sheet = compose_grid([solid(RED), solid(GREEN), solid(BLUE)], columns=2, header_height=5)

    assert sheet.size == (20, 5 + 2 * 8)
    assert sheet.getpixel((0, 0)) == (0, 0, 0)  # header band
    assert sheet.getpixel((5, 5)) == RED
    assert sheet.getpixel((15, 5)) == GREEN
    assert sheet.getpixel((5, 13)) == BLUE
    assert sheet.getpixel((15, 13)) == (0, 0, 0)  # unused cell


def test_none_entries_leave_cells_empty():
    sheet = compose_grid([None, solid(RED)], columns=2, background=(9, 9, 9))
    assert sheet.getpixel((5, 4)) == (9, 9, 9)
    assert sheet.getpixel((15, 4)) == RED


def test_mismatched_images_are_resized_to_cell_size():
    sheet = compose_grid([solid(RED, (40, 32))], columns=1, cell_size=(10, 8))
    assert sheet.size == (10, 8)
    assert sheet.getpixel((5, 4)) == RED


def test_requires_cell_size_when_all_images_are_missing():
    with pytest.raises(ValueError):
        compose_grid([None, None], columns=2)
    assert compose_grid([None], columns=1, cell_size=(4, 4)).size == (4, 4)


def test_rejects_non_positive_columns():
    with pytest.raises(ValueError):
        compose_grid([solid(RED)], columns=0)
