"""Decide where the outputs of every video in a batch go before any of them is written.

Outputs are named after the video's stem (``clip.mp4`` -> ``clip_sheet.jpg``).
Two videos that would write to the same folder with the same stem, such as
``clip.mp4`` and ``clip.mkv``, use their full file name instead
(``clip.mp4_sheet.jpg`` and ``clip.mkv_sheet.jpg``), so neither overwrites the
other. Names of videos that do not collide never change.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class OutputCollisionError(ValueError):
    """Two videos of a batch would still write the same outputs."""


@dataclass(frozen=True)
class OutputSlot:
    folder: Path
    """Folder that receives the outputs of the video."""
    name: str
    """Base name of the outputs: the stem, or the full file name when stems collide."""
    renamed: bool = False
    """True when `name` had to include the extension to avoid a collision."""


def output_folder(video: Path, output: Path | None, source: Path | None) -> Path:
    """Next to the video, or inside `output` mirroring the video's place under `source`."""
    if output is None:
        return video.parent
    if source is not None:
        try:
            return output / video.parent.relative_to(source)
        except ValueError:
            pass  # the video is not under `source`
    return output


def plan_outputs(
    videos: Sequence[Path], *, output: Path | None = None, source: Path | None = None
) -> list[OutputSlot]:
    """One slot per video, in the same order.

    `output` is the folder chosen by the user, if any; `source` is the folder that
    was scanned, so that its subfolders are recreated inside `output`.
    Raises OutputCollisionError when two videos would get the same folder and
    name even after the rename, which can only happen with unusual file names
    such as ``clip.mp4.mkv`` next to ``clip.mp4`` and ``clip.mkv``.
    """
    folders = [output_folder(video, output, source) for video in videos]
    by_stem: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, (video, folder) in enumerate(zip(videos, folders, strict=True)):
        by_stem[_key(folder, video.stem)].append(index)

    slots = [OutputSlot(folder, video.stem) for video, folder in zip(videos, folders, strict=True)]
    for indices in by_stem.values():
        if len(indices) < 2:
            continue
        names = ", ".join(str(videos[i]) for i in indices)
        logger.warning("Videos share a name; their outputs include the extension: %s", names)
        for i in indices:
            slots[i] = OutputSlot(folders[i], videos[i].name, renamed=True)

    by_name: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, slot in enumerate(slots):
        by_name[_key(slot.folder, slot.name)].append(index)
    clashes = [indices for indices in by_name.values() if len(indices) > 1]
    if clashes:
        listed = "; ".join(" and ".join(str(videos[i]) for i in indices) for indices in clashes)
        raise OutputCollisionError(f"These videos would overwrite each other's outputs: {listed}")
    return slots


def _key(folder: Path, name: str) -> tuple[str, str]:
    # normcase folds case on Windows, where clip.mp4 and Clip.mkv share outputs.
    return os.path.normcase(os.path.abspath(folder)), os.path.normcase(name)
