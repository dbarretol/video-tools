# videothumb

Generate contact sheets (a grid of frames under the video's details) and folders of full-size
frames from videos, on the command line or in a desktop app.

## Usage

```bash
# Contact sheet of one video (default: movie_sheet.jpg next to it)
videothumb sheet movie.mp4 --columns 5 --rows 4

# 15 frames of one video (default: a movie_frames folder next to it)
videothumb frames movie.mp4 --count 15

# Every video in two folders and a list, outputs collected in one place
videothumb sheet ./videos ./more/*.mkv --input-list extra.txt -o ./sheets --workers 2

# The desktop app
videothumb gui
```

`videothumb` without arguments shows the commands. `sheet` and `frames` take any number of videos,
folders and patterns, and `--input-list FILE` (see the
[main README](../../README.md#choosing-the-videos)).

| Option | Description | Default |
|---|---|---|
| `PATH...`, `--input-list FILE` | Videos, folders (`-r` for subfolders), patterns, lists of paths | at least one |
| `-o`, `--output` | For one video: the sheet file or frames folder. Otherwise a folder for every output, recreating subfolders with `-r` | next to each video |
| `--columns`, `--rows` (`sheet`) | Contact sheet grid | `5`, `4` |
| `--thumbnail-width` (`sheet`) | Thumbnail width in pixels | `320` |
| `--count` (`frames`) | Number of frames | `10` |
| `--quality` | JPEG quality (1-100) | `90` |
| `--precise` | Take frames at exact positions (slower) | off |
| `-w`, `--workers` | Videos processed in parallel | automatic |
| `--config FILE` | Defaults file to use (see below) | see below |
| `--overwrite` / `--skip-existing` | Replace existing outputs, or leave videos whose sheet or frames folder already exists | `--overwrite` |
| `--dry-run` | Show what would be done for each video, without processing anything | off |
| `--strict` | Treat incomplete outputs (DEGRADED, exit code 3) as failures (exit code 1) | off |
| `-q`, `--quiet` | Only show warnings, errors and what to do about them | off |
| `-v`, `--verbose` | Also show debug messages (they always go to the log file) | off |
| `--open` | Open the output folder when done | off |
| `--json` | Write the plan and the results to stdout as one JSON document (messages go to stderr); see the [main README](../../README.md) | off |

With several videos, a plan is printed first: videos to process and already done, the length of
video to process and a rough time estimate. In a terminal, a progress bar for the folder and one
row per video being processed follow; each finished video leaves one line, and a summary ends the
run.

Each sheet and frame is read back after being written. When fewer than 90% of its frames
could be decoded, the video is reported as DEGRADED (incomplete) instead of succeeded, and the
command exits with 3.

Frames are named after the video and the moment they show, so they can be moved or mixed with
others: `movie_f001_00h12m34s.jpg` is the first frame of `movie.mp4`, at 12 minutes 34 seconds.

The form of earlier versions, `--video`/`--folder` with `--mode sheet|snapshots`, and the
`snapshots` command still work, with a warning that shows the new command; they will be removed.

By default frames are taken from the nearest keyframe at or before each position, which is several
times faster; thumbnails are labeled with the time of the frame actually shown. When two positions
fall on the same keyframe (videos with keyframes far apart, such as screen recordings) or a
keyframe cannot be decoded, that position is taken at its exact time instead, so the sheet never
repeats a frame. Use `--precise` to take every position at its exact time.

Folder scans skip hidden files and folders. When two videos would write to the same place under the
same name, such as `clip.mp4` and `clip.mkv`, their outputs include the extension
(`clip.mp4_sheet.jpg`, `clip.mkv_frames/`) instead of overwriting each other.

## Default settings

The defaults of the command line can be kept in a YAML file, with one section per command (`sheet`, `frames`), a
`run` section for folders and a `console` section (notifications, sleep, the pause when started
from the Explorer; see the [main README](../../README.md)). Flags given on the command line still
take precedence. The desktop app does not read this file.

```bash
videothumb config set sheet.columns 6     # change one default
videothumb config set run.workers 2
videothumb config unset sheet.columns     # back to the built-in default
videothumb config edit    # open the file in an editor (created first if needed)
videothumb config init    # write a commented config file with every option
videothumb config show    # the defaults in effect and where they come from
videothumb config path    # where it is
```

```yaml
sheet:
  columns: 6
  jpeg_quality: 95
  sampling: precise    # like --precise
frames:
  count: 15
run:
  recursive: true      # --no-recursive turns it off for one run
  workers: 2
```

The file is `%APPDATA%\videothumb\config.yaml` on Windows and
`$XDG_CONFIG_HOME/videothumb/config.yaml` elsewhere; `--config FILE` or the `VIDEOTHUMB_CONFIG`
environment variable select another one. `config set` checks the value and changes only that
line, keeping your comments. Invalid values are reported with the file name and nothing is
processed.

Logs, the desktop app's settings and `state.json` (how fast each command ran on this computer, to
estimate the time of the next batches) are stored in `%LOCALAPPDATA%\videothumb` on Windows and
`$XDG_STATE_HOME/videothumb` elsewhere.
