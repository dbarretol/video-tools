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
| `-o`, `--output` | With `--video`: the sheet file or snapshot folder. With `--folder`: a folder for every output | next to each video |
| `--columns`, `--rows` | Contact sheet grid | `5`, `4` |
| `--thumbnail-width` | Thumbnail width in pixels | `320` |
| `--count` | Number of snapshots | `10` |
| `--quality` | JPEG quality (1-100) | `90` |
| `--precise` | Take frames at exact positions (slower) | off |
| `-w`, `--workers` | Videos processed in parallel with `--folder` | half the CPU threads |

By default frames are taken from the nearest keyframe at or before each position, which is several
times faster; thumbnails are labeled with the time of the frame actually shown. Use `--precise`
when exact positions matter.

Logs and the desktop app's settings are stored in `%LOCALAPPDATA%\videothumb` on Windows and
`$XDG_STATE_HOME/videothumb` elsewhere.
