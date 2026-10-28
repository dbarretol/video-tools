# video-tools

Python tools for previewing and archiving video files, built on [PyAV](https://pyav.basswood-io.com/)
(FFmpeg's libraries, bundled: no separate FFmpeg install needed).

| Tool | What it does |
|---|---|
| [**videothumb**](packages/videothumb) | Contact sheets (a grid of thumbnails under a metadata header) and individual snapshots, for one video or a whole folder. Desktop app and command line. |
| [**videosemanticreducer**](packages/videosemanticreducer) | Archives videos before you delete them: a short summary video, a thumbnail grid, a metadata report and a perceptual fingerprint that can later tell whether another file is the same video. Command line. |

Both share [**videocore**](packages/videocore), a small library for probing videos, sampling frames,
composing grids and processing folders in parallel.

## Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)

Developed and tested on Windows 11; the code has no Windows-specific dependencies.

## Installation

Clone the repository and install the tools you want as commands on your `PATH`:

```bash
git clone https://github.com/dbarretol/video-tools.git
cd video-tools
uv tool install ./packages/videothumb
uv tool install ./packages/videosemanticreducer
```

To try them without installing, run them from the clone with `uv run` (the first run creates the
environment):

```bash
uv run videothumb --help
uv run videosemanticreducer --help
```

## Quick start

```bash
# Open the videothumb desktop app
videothumb

# Contact sheet of one video, snapshots of every video in a folder
videothumb --video movie.mp4 --mode sheet
videothumb --folder ./videos --mode snapshots --count 15

# Archive a video: movie_summary/ with summary video, grid, report and fingerprint
videosemanticreducer process movie.mp4

# Later: is this download the same video?
videosemanticreducer verify downloaded.mp4 movie_summary/movie.fingerprint.json
```

Each tool's README documents every option: [videothumb](packages/videothumb/README.md),
[videosemanticreducer](packages/videosemanticreducer/README.md).

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
  videothumb/             contact sheets and snapshots: CLI + tkinter desktop app
  videosemanticreducer/   archive pipeline, fingerprints, compare/verify
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
