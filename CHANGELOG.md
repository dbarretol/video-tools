# Changelog

## Unreleased

Breaking changes; the old forms keep working for one version, with a warning that shows the new
command.

### videothumb

- Commands: `videothumb sheet PATH...` and `videothumb frames PATH...` replace
  `--video`/`--folder` with `--mode sheet|snapshots`. Each command only accepts its own options.
- `videothumb` without arguments shows the help; the desktop app opens with `videothumb gui`.
- "Snapshots" are now "frames": the folder is `<name>_frames/` instead of `<name>/`, and each
  file is named after the video and its time, `<name>_f001_00h12m34s.jpg`, instead of
  `snapshot_001.jpg`. The config section `snapshots:` is now `frames:`.

### Both tools

- Any number of videos, folders and patterns (`*.mp4`), and `--input-list FILE` with one path per
  line. Listed paths that do not exist fail that video only.
- Live progress, `-q/--quiet`, `--json`, `--open`; logs reach the console only with `-v`.
- On Windows: no sleep while processing, a notification after long runs, and the window stays
  open when started from the Explorer (`console` section of the config file).
- Exit code 3 when some outputs are incomplete (`--strict` turns it into 1).

### videosemanticreducer

- `status PATH...` checks archives before originals are deleted.
- `fingerprint` takes several videos.
