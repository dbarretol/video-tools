# video-tools

Python tools for previewing and archiving video files, built on [PyAV](https://pyav.basswood-io.com/)
(FFmpeg's libraries, bundled: no separate FFmpeg install needed).

| Tool | What it does |
|---|---|
| [**videothumb**](packages/videothumb) | Contact sheets (a grid of frames under the video's details) and folders of full-size frames, apart or in one ZIP, for any number of videos and folders. Desktop app and command line. |
| [**videosemanticreducer**](packages/videosemanticreducer) | Archives videos before you delete them: a short summary video, a contact sheet, a metadata report and a perceptual fingerprint that can later tell whether another file is the same video. Command line. |

[**vtools**](packages/vtools) is one command for both: `vtools sheet`, `vtools archive`, ...

Both share [**videocore**](packages/videocore), a small library for probing videos, sampling frames,
composing grids and processing folders in parallel, and [**videoconsole**](packages/videoconsole),
which draws their progress and reports in the terminal.

## Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)

Developed and tested on Windows 11; the code has no Windows-specific dependencies.

## Installation

Clone the repository and install the tools as commands on your `PATH`:

```bash
git clone https://github.com/dbarretol/video-tools.git
cd video-tools
uv tool install ./packages/vtools --with-executables-from videothumb,videosemanticreducer
```

That installs `vtools` and both tools with their short names: `videothumb` (`vthumb`) and
`videosemanticreducer` (`vsr`). `uv tool upgrade vtools` updates them all. A single tool can be
installed alone instead, e.g. `uv tool install ./packages/videothumb`.

To try them without installing, run them from the clone with `uv run` (the first run creates the
environment):

```bash
uv run vtools --help
uv run vthumb --help
uv run vsr --help
```

## Quick start

```bash
# Contact sheet of one video (without a command, `sheet` runs), 15 frames of each video in a folder
vtools movie.mp4
vtools frames ./videos --count 15

# The sheet and its frames in one ZIP per video
vtools pack ./videos

# Open the videothumb desktop app
vtools gui

# Archive a video: movie_summary/ with summary video, contact sheet, report and fingerprint
vtools archive movie.mp4

# Later: is this download the same video?
vtools verify downloaded.mp4 movie_summary/movie.fingerprint.json
```

The same commands work with each tool: `vthumb movie.mp4`, `vsr archive movie.mp4`. Without a
command, `vthumb` runs `sheet` and `vsr` runs `archive`, so a video dropped on the program in the
Explorer is processed right away; `console.default_task` in the config file chooses another
(`frames` or `pack`; `fingerprint` or `status`). A command name always wins over a file with the
same name: write `.\sheet` for that file.

Each tool's README documents every option: [videothumb](packages/videothumb/README.md),
[videosemanticreducer](packages/videosemanticreducer/README.md).

While a folder is processed, the terminal shows a bar for the whole batch (with the time
remaining) and one row per video being processed, with the stage it is in; each finished video
leaves one line. When the output is redirected, only those lines are printed. Both tools accept:

- `-q/--quiet`: only warnings, errors and what to do about them (nothing at all when every video
  succeeds);
- `-v/--verbose`: debug messages as well. Without it, log messages only go to the log file, in
  `%LOCALAPPDATA%\<tool>\logs`.

On Windows, while videos are processed the computer does not go to sleep (the screen still turns
off), and a run that took 5 minutes or more ends with a desktop notification. Started from the
Explorer (double click, a shortcut, or files dropped on the program), the window stays open at the
end until Enter is pressed, and typing `O` first opens the output folder; `--open` opens it in any
case. These are set in the `console` section of each tool's config file:

```yaml
console:
  notify_after: 5    # minutes; 0 turns notifications off
  keep_awake: true
  pause: true        # wait for Enter when started from the Explorer
  default_task: sheet  # what the tool does with paths and no command
```

## Choosing the videos

`videothumb sheet`, `frames` and `pack`, and `videosemanticreducer archive`, `fingerprint` and `status`
take any number of `PATH` arguments:
videos, folders (their videos; subfolders too with `-r`) and patterns such as `D:\Videos\*.mkv`,
which are expanded by the tool because the Windows shell does not. A path that exists is used as
it is, so names with brackets such as `clip [1080p].mp4` work; only a path that does not exist is
read as a pattern. Videos given twice are processed once.

`--input-list FILE` (repeatable) reads one path per line from a text file: empty lines and lines
starting with `#` are skipped, quotes around a path are removed (as added by the Explorer's
"Copy as path"), relative paths are relative to the list, and the file is UTF-8, with or without
BOM. A listed path that does not exist does not stop the run: it is reported as failed and goes to
the list of videos to retry. The retry lists of earlier runs are input lists too.

`--input-list -` reads standard input, relative to the current folder. Windows PowerShell 5.1
breaks names with accents when piping to a program; write a UTF-8 file instead:

```powershell
Get-ChildItem D:\Videos -Recurse -Filter *.mp4 | ForEach-Object FullName |
    Set-Content -Encoding utf8 list.txt
videosemanticreducer archive --input-list list.txt
```

## Exit codes

Both tools use the same exit codes, so scripts can tell what happened:

| Code | Meaning |
|---:|---|
| 0 | Everything succeeded (videos skipped because their output already exists count as success) |
| 1 | At least one video failed; for `verify` and `compare`, the videos do not match |
| 2 | Invalid arguments, a missing input, or an invalid config file; nothing was processed |
| 3 | Nothing failed, but some outputs are incomplete (DEGRADED); `--strict` turns this into 1 |
| 130 | Interrupted with Ctrl+C; unfinished files were removed |

After a folder run with failures, the videos to retry are listed in a text file, one path per
line; after Ctrl+C, so are the videos not finished, and the summary says how to resume. The files
are kept in `%LOCALAPPDATA%\<tool>\runs` (the last 20 runs).

When a video fails, the error is followed by a hint for the common causes (a damaged or
unsupported file, no video stream, a file only in OneDrive, a full disk, denied access, a file
moved during the run) and by the path of the log file, which has the technical details.

Every output is read back right after being written. A video whose outputs are written but
incomplete, for example a contact sheet where fewer than 90% of the thumbnails could be decoded,
is reported as DEGRADED. A script that only accepts complete results can check for it:

```powershell
videothumb sheet D:\Videos
if ($LASTEXITCODE -eq 3) { Write-Warning "some sheets are incomplete" }
elseif ($LASTEXITCODE -ne 0) { throw "videothumb failed ($LASTEXITCODE)" }
```

With `--json` (`videothumb`, and `videosemanticreducer archive` and `status`), stdout receives a
single JSON document and every message goes to stderr. It has the plan (each video with what will
happen to it, the length to process and the estimated time), the result of each video (status,
seconds, error, hint, problems), the lists of videos to retry, the exit code and the log file; an
error that stops the command is in `error`. It is plain ASCII, so it reads the same in any console
encoding. `schema` is its version.

```powershell
$run = videothumb sheet D:\Videos --json | ConvertFrom-Json
$run.report.items | Where-Object status -eq "failed" | Select-Object video, error, hint
```

## Performance

Frames are decoded in-process and only where needed: keyframe seeking for previews, precise
seeking where exact positions matter, decoder-side scaling, and several decoders in parallel
threads. On a 61-minute 1080p60 H.264 recording (4-core laptop CPU, no GPU):

| Job | Time |
|---|---:|
| 5x4 contact sheet | ~1 s |
| Full archive (150-snapshot summary video, 12x12 grid, fingerprint) | ~20–25 s |

Folders are processed in parallel processes; one failing file never stops the rest.

## Repository layout

```
packages/
  videocore/              shared library (probe, frame sampling, grids, batch, logging)
  videoconsole/           terminal output of the command lines (rich): progress and reports
  videothumb/             contact sheets, frames and packs: CLI + tkinter desktop app
  videosemanticreducer/   archive pipeline, fingerprints, compare/verify
  vtools/                 one command for both tools (it only routes)
benchmarks/               timing harness (bench.py) and drivers for the tools
```

This is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): one lock file,
one virtual environment, and each tool is a separate package with its own command.

## Development

```bash
uv sync                      # create .venv with every package and the dev tools
uv run pytest                # run all tests
uv run ruff check .          # lint
uv run ruff format .         # format
```

Tests generate tiny synthetic videos on the fly; no media files are stored in the repository.

To time the tools on your own videos:

```bash
uv run python benchmarks/bench.py --targets benchmarks/targets.toml --video path/to/video.mkv
```

See [benchmarks/bench.py](benchmarks/bench.py) for options (repeats, warm-up, selected targets).

## License

[MIT](LICENSE)
