import json
import shutil

import pytest

from videosemanticreducer.cli import main
from videosemanticreducer.pipeline import (
    ARCHIVE_COMPLETE,
    ProcessOptions,
    archive_state,
    process_video,
)
from videosemanticreducer.status import ArchiveStatus, check_archive, find_archives

QUICK = ProcessOptions(summary=False, sheet_columns=2, sheet_rows=2)


@pytest.fixture
def videos(clip, tmp_path):
    folder = tmp_path / "videos"
    folder.mkdir()
    for name in ("a.mp4", "b.mp4"):
        shutil.copy(clip, folder / name)
    return folder


def archive(video, output=None):
    output = output or video.parent / f"{video.stem}_summary"
    process_video(video, output, QUICK)
    return output


def statuses(checks):
    return [(check.label, check.status) for check in checks]


def test_a_complete_archive_is_ready(videos):
    folder = archive(videos / "a.mp4")
    check = check_archive(folder, "a", videos / "a.mp4", full=True)
    assert check.status is ArchiveStatus.READY
    assert check.details == []


def test_no_manifest_means_not_archived(videos):
    assert check_archive(videos / "a_summary", "a").status is ArchiveStatus.MISSING


def test_modified_or_missing_artifacts_are_reported(videos):
    folder = archive(videos / "a.mp4")
    (folder / "a_info.txt").unlink()
    with (folder / "a_sheet.jpg").open("ab") as thumb:
        thumb.write(b"x")
    check = check_archive(folder, "a")
    assert check.status is ArchiveStatus.CHANGED
    assert check.details == ["a_info.txt is missing", "a_sheet.jpg was modified"]


def test_full_compares_hashes(videos):
    folder = archive(videos / "a.mp4")
    thumb = folder / "a_sheet.jpg"
    data = bytearray(thumb.read_bytes())
    data[-10] ^= 0xFF  # same size, other content
    thumb.write_bytes(bytes(data))
    assert check_archive(folder, "a").status is ArchiveStatus.READY
    assert check_archive(folder, "a", full=True).details == ["a_sheet.jpg was modified"]


def test_a_replaced_original_is_not_ready(videos, audio_only):
    folder = archive(videos / "a.mp4")
    shutil.copy(audio_only, videos / "a.mp4")
    check = check_archive(folder, "a", videos / "a.mp4")
    assert check.status is ArchiveStatus.CHANGED
    assert "is not the video that was archived" in check.details[0]


def test_failed_and_incomplete_runs_are_reported(videos):
    folder = archive(videos / "a.mp4")
    manifest_file = folder / "a.archive.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    manifest.update(complete=False, problems=["thumbnail grid: only 1/4 frames could be decoded"])
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    check = check_archive(folder, "a")
    assert check.status is ArchiveStatus.INCOMPLETE
    assert check.details == manifest["problems"]

    manifest.update(problems=[], error="VideoOpenError: Could not open video")
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    assert check_archive(folder, "a").status is ArchiveStatus.FAILED


def test_folders_pair_videos_with_archives_and_find_orphans(videos):
    archive(videos / "a.mp4")
    archive(videos / "b.mp4")
    (videos / "b.mp4").unlink()  # the original was deleted after archiving
    shutil.copy(videos / "a.mp4", videos / "c.mp4")

    checks = find_archives(videos)
    assert statuses(checks) == [
        ("a.mp4", ArchiveStatus.READY),
        ("c.mp4", ArchiveStatus.MISSING),
        ("b.mp4", ArchiveStatus.READY),
    ]
    assert "the original is no longer at" in checks[2].details[0]
    # An archive folder can also be checked by itself.
    assert statuses(find_archives(videos / "a_summary")) == [("a.mp4", ArchiveStatus.READY)]


def test_archives_in_an_output_folder(videos, tmp_path):
    out = tmp_path / "out"
    archive(videos / "a.mp4", out / "a_summary")
    checks = find_archives(videos, output=out)
    assert statuses(checks) == [("a.mp4", ArchiveStatus.READY), ("b.mp4", ArchiveStatus.MISSING)]


def test_cli(videos, capsys):
    archive(videos / "a.mp4")
    assert main(["status", str(videos / "a.mp4")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("READY")
    assert "1 checked: 1 ready to delete" in out
    assert "use --full" in out

    assert main(["status", str(videos), "--full"]) == 1
    out = capsys.readouterr().out
    assert "NOT ARCHIVED  b.mp4" in out
    assert "2 checked: 1 ready to delete, 1 not archived" in out
    assert "use --full" not in out

    assert main(["status", str(videos / "missing.mp4")]) == 2


def test_cli_finds_archives_where_process_put_them_with_the_config(videos, tmp_path):
    out = tmp_path / "out"
    config = tmp_path / "c.yaml"
    lines = ["archive:", f"  output: {out.as_posix()}", "  summary: false"]
    lines += ["  sheet_columns: 2", "  sheet_rows: 2"]
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with_config = ["--config", str(config)]
    assert main(["archive", str(videos), "-w", "1", *with_config]) == 0
    assert main(["status", str(videos), *with_config]) == 0
    assert main(["status", str(videos)]) == 1  # without it, the archives are not next to them


def make_old(folder, name):
    """Turn an archive into one made before 0.2: `_thumb.jpg` and the old option names."""
    (folder / f"{name}_sheet.jpg").rename(folder / f"{name}_thumb.jpg")
    path = folder / f"{name}.archive.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for output in manifest["outputs"]:
        output["file"] = output["file"].replace("_sheet.jpg", "_thumb.jpg")
    options = manifest["options"]
    options["thumbnail"] = options.pop("sheet")
    options["thumb_cols"] = options.pop("sheet_columns")
    options["thumb_rows"] = options.pop("sheet_rows")
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_archives_made_before_the_rename_still_read(videos):
    folder = archive(videos / "a.mp4")
    make_old(folder, "a")
    assert check_archive(folder, "a", videos / "a.mp4", full=True).status is ArchiveStatus.READY
    assert archive_state(folder, "a", QUICK) == ARCHIVE_COMPLETE  # not outdated: same options

    process_video(videos / "a.mp4", folder, QUICK)  # archived again: the old name goes away
    assert (folder / "a_sheet.jpg").is_file() and not (folder / "a_thumb.jpg").exists()
