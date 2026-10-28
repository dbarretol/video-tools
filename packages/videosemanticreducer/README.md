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
```

`process` options: `-n/--num-snapshots` (150), `-d/--snap-duration` (0.4 s),
`--thumb-cols`/`--thumb-rows` (12x12), `--no-video`, `--no-thumbnail`, `--no-fingerprint`,
`-r/--recursive`, `-w/--workers`.

## Artifacts

For `movie.mp4`:

| File | Content |
|---|---|
| `movie_summary.mp4` | An 8 s info card followed by evenly spaced snapshots |
| `movie_thumb.jpg` | Grid of thumbnails covering the whole video |
| `movie_info.txt` | Container, stream and tag metadata |
| `movie.fingerprint.json` | SHA-256, global perceptual hashes and per-position hashes |

## Fingerprint format

Version `2.0` takes every hashed frame with precise seeking, so the hashes depend on the content
and not on where the encoder placed keyframes. Fingerprints in format `1.0` can still be compared:
their global hashes were taken from keyframes only, so that layer is skipped and the match level is
decided by the file hash and the per-position hashes.
