# videosemanticreducer

Archive videos before deleting them. For each video it produces a lightweight summary video, a
thumbnail grid, a plain-text metadata report and a perceptual fingerprint that can later be used to
verify whether a re-downloaded file is the same video.

## Usage

```bash
# One video: artifacts go to movie_summary/ next to it
videosemanticreducer process movie.mp4

# Every video in a folder, several in parallel
videosemanticreducer process D:/Videos -o D:/Archive --workers 2

# Fingerprint only, compare two fingerprints, verify a file against a saved fingerprint
videosemanticreducer fingerprint movie.mp4
videosemanticreducer compare a.fingerprint.json b.fingerprint.json
videosemanticreducer verify downloaded.mp4 movie.fingerprint.json

# Before deleting originals: are their archives complete and unchanged?
videosemanticreducer status D:/Videos -o D:/Archive --full
```

`process` options: `-n/--num-snapshots` (150), `-d/--snap-duration` (0.4 s),
`--thumb-cols`/`--thumb-rows` (12x12), `--no-video`, `--no-thumbnail`, `--no-fingerprint`,
`-r/--recursive`, `-w/--workers`, `-o/--output`, `--skip-existing` (default) / `--overwrite`,
`--dry-run`.
`-v/--verbose` shows debug messages and works before or after the command name.

Videos whose archive is already complete are skipped, so an interrupted or partly failed run over
a folder can simply be started again. A video is archived again when its manifest is missing or
incomplete, when one of its files changed or disappeared, or when an artifact is requested that
was not made the first time (for example `--fingerprint` after a `--no-fingerprint` run). Archives
made with other settings, such as another grid size, are reported and kept; `--overwrite` redoes
every video.

Before processing a folder, `process` reports its plan: how many videos will be archived, how
many are already done or were done with other settings, the length of video to process, a rough
time estimate and files that are only in OneDrive (reading them downloads them). `--dry-run`
prints that plan with one line per video and stops without writing anything.
When a single video is archived, each step is logged as it starts (summary video, thumbnail
grid, fingerprint...); with `-v`, batches log them too.

`verify` and `compare` exit with 0 when the videos match, 1 when they do not (or the match is
uncertain) and 2 when a file cannot be read.

Folder scans skip hidden files and folders (such as macOS `._movie.mp4` files), and recursive scans
skip the `<stem>_summary` folders written by earlier runs, so summary videos are never archived
again. With `-o` and `-r`, subfolders are recreated inside the output folder. Two videos with the
same stem in the same place, such as `clip.mp4` and `clip.mkv`, get `clip.mp4_summary/` and
`clip.mkv_summary/` instead of sharing one folder.

A video only counts as archived when its fingerprint is complete, since the fingerprint is what
later proves that a copy is the same video. The run reports a failure, and writes no fingerprint,
when the file has no video stream, when no frame could be hashed for the global hashes, or when
fewer than half of the per-position frames could be hashed.

Every artifact is read back before the manifest is written: the summary video must open and last
as long as expected, the grid must have the expected size and the fingerprint must load. An
unreadable artifact fails the video. A summary or grid with fewer than 90% of its frames decoded
makes the archive DEGRADED: it is kept, its manifest lists the `problems` and is not marked
complete, so the next run archives the video again. The command then exits with 3, or with 1
with `--strict`.

## Checking archives before deleting originals

`status PATH...` takes videos, archive folders or folders of videos (`-r` for subfolders; `-o` and
`--config` as given to `process`) and prints one line per video:

| Status | Meaning |
|---|---|
| `READY` | The manifest says the archive is complete, every artifact is there with its size, and the original, if still present, is the video archived. The original can be deleted. |
| `INCOMPLETE` | Archived, but some artifacts are incomplete; run `process` again |
| `FAILED` | The archive run failed or did not finish |
| `CHANGED` | An artifact is missing or was modified, or the original is not the video archived |
| `NOT ARCHIVED` | No archive was found where `process` would put it |

Archives whose original was already deleted are listed too. `--full` also compares the SHA-256
of every artifact and of the original with those in the manifest, which reads every file; use it
before deleting. The exit code is 0 only when every archive is `READY`.

## Default settings

The defaults of `process` can be changed in a YAML file instead of passing the same flags every
time. Flags given on the command line still take precedence.

```bash
videosemanticreducer config set num_snapshots 100   # change one default
videosemanticreducer config unset num_snapshots     # back to the built-in default
videosemanticreducer config edit   # open the file in an editor (created first if needed)
videosemanticreducer config init   # write a commented config file with every option
videosemanticreducer config path   # where it is
videosemanticreducer config show   # the defaults in effect and where they come from
```

`config set` checks the value before writing it and changes only that line, keeping your comments.
A relative path given to `config set output` is taken from the current folder. The editor is
`$VISUAL` or `$EDITOR` when set, otherwise the system's editor for text files.

```yaml
process:
  num_snapshots: 100
  thumb_cols: 10
  thumb_rows: 10
  output: D:/Archive   # relative paths are relative to the config file
```

The file is `%APPDATA%\videosemanticreducer\config.yaml` on Windows and
`$XDG_CONFIG_HOME/videosemanticreducer/config.yaml` elsewhere. Another file can be used with
`--config FILE` or the `VIDEOSEMANTICREDUCER_CONFIG` environment variable. Unknown options,
values of the wrong type and values out of range (such as `num_snapshots: 0`) are reported as errors
that name the file, and nothing is processed.

The time estimate printed before a batch learns from earlier runs how fast archiving is on this
computer. That measure is kept in `state.json`, next to the logs, and can be deleted at any time.

## Artifacts

For `movie.mp4`:

| File | Content |
|---|---|
| `movie_summary.mp4` | An 8 s info card followed by evenly spaced snapshots |
| `movie_thumb.jpg` | Grid of thumbnails covering the whole video |
| `movie_info.txt` | Container, stream and tag metadata |
| `movie.fingerprint.json` | SHA-256, global perceptual hashes and per-position hashes |
| `movie.archive.json` | Manifest, written last: the source video, the options used and every file above with its size and SHA-256 |

The manifest says `"complete": true` only when every requested file was written. A failed run writes
it with `"complete": false` and the error, and an interrupted run leaves none, so a folder only
counts as a finished archive when its manifest says so. Files are written under a temporary
`.part` name and renamed when finished, so a file with its final name is never truncated; partial
files left by a cancelled or interrupted run are removed.

## Fingerprint format

Version `2.0` takes every hashed frame with precise seeking, so the hashes depend on the content
and not on where the encoder placed keyframes. Fingerprints in format `1.0` can still be compared:
their global hashes were taken from keyframes only, so that layer is skipped and the match level is
decided by the file hash and the per-position hashes.
