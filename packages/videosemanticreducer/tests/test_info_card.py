import dataclasses

from videocore import probe
from videosemanticreducer.info_card import BACKGROUND, card_lines, render_info_card


def test_card_lines_summarize_the_video(clip):
    lines = card_lines(probe(clip))
    assert "Resolution: 160x90" in lines
    assert "Codecs: h264 / aac" in lines
    assert "Title: vsr test clip" in lines


def test_long_tags_are_truncated(clip):
    info = dataclasses.replace(probe(clip), tags={"COMMENT": "x" * 200})
    (comment,) = [line for line in card_lines(info) if line.startswith("Comment:")]
    assert comment == "Comment: " + "x" * 77 + "..."


def test_render_has_requested_size_and_text(clip):
    card = render_info_card(probe(clip), (640, 360))
    assert card.size == (640, 360)
    assert card.mode == "RGB"
    colors = {color for _, color in card.getcolors(maxcolors=100_000)}
    assert BACKGROUND in colors
    assert len(colors) > 1  # something was drawn on the background


def test_render_audio_only(audio_only):
    card = render_info_card(probe(audio_only), (320, 180))
    assert card.size == (320, 180)
