# videosemanticreducer

Archive videos before deleting them. For each video it produces a lightweight summary video, a
contact sheet (a grid of frames), a plain-text metadata report and a perceptual fingerprint that can later be used to
verify whether a re-downloaded file is the same video.

## Usage

```bash
# One video: artifacts go to movie_summary/ next to it
videosemanticreducer archive movie.mp4

# Every video in a folder, several in parallel
videosemanticreducer archive D:/Videos -o D:/Archive --workers 2

# Several folders and files, a pattern, and the videos listed in a text file
videosemanticreducer archive D:/Videos E:/Clips/*.mkv --input-list to-archive.txt -o D:/Archive

# Fingerprint only, compare two fingerprints, verify a file against a saved fingerprint
videosemanticreducer fingerprint movie.mp4
videosemanticreducer compare a.fingerprint.json b.fingerprint.json
videosemanticreducer verify downloaded.mp4 movie.fingerprint.json

# Before deleting originals: are their archives complete and unchanged?
videosemanticreducer status D:/Videos -o D:/Archive --full
```

`vsr` is the same program, with a shorter name. Paths without a command are archived
(`vsr movie.mp4`), unless `console.default_task` in the config file says `fingerprint` or `status`.

`archive`, `fingerprint` and `status` take any number of videos, folders and patterns, and
`--input-list FILE` (see the [main README](../../README.md#choosing-the-videos)). With a single
video, `-o` is its archive folder; otherwise each video gets `<name>_summary/` inside `-o`.
A path in a list that does not exist fails that video only.

`archive` options: `-n/--num-snapshots` (150), `-d/--snap-duration` (0.4 s),
`--grid CxR` (contact sheet columns and rows, 12x12), `--no-summary`, `--no-sheet`,
`--no-fingerprint`, `-r/--recursive`, `-w/--workers`, `-o/--output`, `--skip-existing`
(default) / `--overwrite`, `--dry-run`, `--open` (open the output folder when done),
`--no-progress` (one line per video instead of the progress bar).
`-v/--verbose` also shows debug messages, including the stage each video is in, and `-q/--quiet`
only shows warnings, errors and what to do about them; both work before or after the command
name. Log messages always go to the log file. In a terminal, `archive` shows a progress bar for
the folder and one row per video being archived, with its current stage. `status -q` only lists
the archives that are not ready. `archive --json` and `status --json` write one JSON document to
stdout (the plan and results, or one entry per archive with `status` `ready`, `incomplete`,
`failed`, `changed` or `not_archived`); messages then go to stderr.

Videos whose archive is already complete are skipped, so an interrupted or partly failed run over
a folder can simply be started again. A video is archived again when its manifest is missing or
incomplete, when one of its files changed or disappeared, or when an artifact is requested that
was not made the first time (for example `--fingerprint` after a `--no-fingerprint` run). Archives
made with other settings, such as another grid size, are reported and kept; `--overwrite` redoes
every video.

Before processing a folder, `archive` reports its plan: how many videos will be archived, how
many are already done or were done with other settings, the length of video to process, a rough
time estimate and files that are only in OneDrive (reading them downloads them). `--dry-run`
prints that plan with one line per video and stops without writing anything.
When a single video is archived, each step is logged as it starts (summary video, contact
sheet, fingerprint...); with `-v`, batches log them too.

`verify` and `compare` exit with 0 when the videos match, 1 when they do not (or the match is
uncertain) and 2 when a file cannot be read.

The names of earlier versions still work, with a warning that shows the new one; they will be
removed: `process` (now `archive`), `--video/--no-video` (`--summary/--no-summary`),
`--thumbnail/--no-thumbnail` (`--sheet/--no-sheet`) and `--thumb-cols N`/`--thumb-rows N`
(`--grid CxR`).

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
`--config` as given to `archive`) and prints one line per video:

| Status | Meaning |
|---|---|
| `READY` | The manifest says the archive is complete, every artifact is there with its size, and the original, if still present, is the video archived. The original can be deleted. |
| `INCOMPLETE` | Archived, but some artifacts are incomplete; run `archive` again |
| `FAILED` | The archive run failed or did not finish |
| `CHANGED` | An artifact is missing or was modified, or the original is not the video archived |
| `NOT ARCHIVED` | No archive was found where `archive` would put it |

Archives whose original was already deleted are listed too. `--full` also compares the SHA-256
of every artifact and of the original with those in the manifest, which reads every file; use it
before deleting. The exit code is 0 only when every archive is `READY`.

## Default settings

The defaults of `archive` can be changed in a YAML file instead of passing the same flags every
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
archive:
  num_snapshots: 100
  sheet_columns: 10    # --grid 10x8 on the command line
  sheet_rows: 8
  output: D:/Archive   # relative paths are relative to the config file
console:
  notify_after: 10     # minutes a run must take to end with a notification; 0 = never
```

The `console` section also has `keep_awake` and `pause` (see the [main README](../../README.md)).
Option names are unique across sections, so `config set notify_after 10` works too.

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
| `movie_sheet.jpg` | Contact sheet: a grid of frames covering the whole video (`movie_thumb.jpg` in archives made before 0.2, which `status` still checks; archiving again replaces it) |
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
