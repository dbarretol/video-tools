# videothumb

Generate contact sheets (a grid of thumbnails with video metadata) and individual snapshots from
one video or a whole folder, from a desktop GUI or the command line.

## Usage

Run `videothumb` without arguments to open the desktop app. With arguments it runs on the command
line:

```bash
# Contact sheet of one video (default: movie_sheet.jpg next to it)
videothumb --video movie.mp4 --mode sheet --columns 5 --rows 4

# 15 snapshots of one video (default: a "movie" folder next to it)
videothumb --video movie.mp4 --mode snapshots --count 15

# Every video in a folder, outputs collected in one place
videothumb --folder ./videos --mode sheet -o ./sheets --workers 2
```

| Option | Description | Default |
|---|---|---|
| `--video` / `--folder` | One video, or every video in a folder (`-r` for subfolders) | required |
| `--mode {sheet,snapshots}` | Output type | required |
| `-o`, `--output` | With `--video`: the sheet file or snapshot folder. With `--folder`: a folder for every output, recreating subfolders with `-r` | next to each video |
| `--columns`, `--rows` | Contact sheet grid | `5`, `4` |
| `--thumbnail-width` | Thumbnail width in pixels | `320` |
| `--count` | Number of snapshots | `10` |
| `--quality` | JPEG quality (1-100) | `90` |
| `--precise` | Take frames at exact positions (slower) | off |
| `-w`, `--workers` | Videos processed in parallel with `--folder` | half the CPU threads |
| `--config FILE` | Defaults file to use (see below) | see below |
| `--overwrite` / `--skip-existing` | Replace existing outputs, or leave videos whose sheet or snapshot folder already exists | `--overwrite` |
| `--dry-run` | Show what would be done for each video, without processing anything | off |

With `--folder`, a plan is printed first: videos to process and already done, the length of
video to process and a rough time estimate.

Options that do not apply to the selected mode, such as `--count` with `--mode sheet`, are
ignored with a warning.

By default frames are taken from the nearest keyframe at or before each position, which is several
times faster; thumbnails are labeled with the time of the frame actually shown. Use `--precise`
when exact positions matter.

Folder scans skip hidden files and folders. When two videos would write to the same place under the
same name, such as `clip.mp4` and `clip.mkv`, their outputs include the extension
(`clip.mp4_sheet.jpg`, `clip.mkv_snapshots/`) instead of overwriting each other.

## Default settings

The defaults of the command line can be kept in a YAML file, with one section per mode and a
`run` section for folders. Flags given on the command line still take precedence. The desktop app
does not read this file.

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
snapshots:
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

Logs, the desktop app's settings and `state.json` (how fast each mode ran on this computer, to
estimate the time of the next batches) are stored in `%LOCALAPPDATA%\videothumb` on Windows and
`$XDG_STATE_HOME/videothumb` elsewhere.
