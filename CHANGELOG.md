# Changelog

## Unreleased

Breaking changes; the old forms keep working for one version, with a warning that shows the new
command.

### videothumb

- Commands: `videothumb sheet PATH...` and `videothumb frames PATH...` replace
  `--video`/`--folder` with `--mode sheet|snapshots`. Each command only accepts its own options.
- New `videothumb pack PATH...`: one ZIP per video, `<name>_pack.zip`, with the contact sheet
  and a `frames/` folder, decoding the video once.
- `videothumb` without arguments shows the help; the desktop app opens with `videothumb gui`.
- "Snapshots" are now "frames": the folder is `<name>_frames/` instead of `<name>/`, and each
  file is named after the video and its time, `<name>_f001_00h12m34s.jpg`, instead of
  `snapshot_001.jpg`. The config section `snapshots:` is now `frames:`.

### vtools

- New package and command: `vtools sheet|frames|pack|gui|archive|fingerprint|compare|verify|status`
  runs the command with the tool that has it; `vtools config thumb|vsr ...` manages their config
  files. `uv tool install ./packages/vtools --with-executables-from videothumb,videosemanticreducer`
  installs everything.

### Both tools

- Short names: `vthumb` (videothumb) and `vsr` (videosemanticreducer).
- Help: `help` and `help COMMAND`, and `/?`, `-?` and `/help` as well as `--help`. The help of
  each tool ends with examples.
- Paths without a command run the default task: `sheet` and `archive`, or `console.default_task`
  of the config file.

- Any number of videos, folders and patterns (`*.mp4`), and `--input-list FILE` with one path per
  line. Listed paths that do not exist fail that video only.
- Live progress (`--no-progress` for one line per video), `-q/--quiet`, `--json`, `--open`; logs reach the console only with `-v`.
- On Windows: no sleep while processing, a notification after long runs, and the window stays
  open when started from the Explorer (`console` section of the config file).
- Exit code 3 when some outputs are incomplete (`--strict` turns it into 1).

### videosemanticreducer

- `archive` replaces `process`; `--summary/--no-summary` replace `--video/--no-video`.
- The thumbnail grid is now the contact sheet: `<name>_sheet.jpg` instead of `<name>_thumb.jpg`,
  `--sheet/--no-sheet` instead of `--thumbnail/--no-thumbnail`, and `--grid CxR` instead of
  `--thumb-cols`/`--thumb-rows`. Archives made before are still read by `status` and are not
  redone; archiving one again replaces its `_thumb.jpg`.
- Config file: the section `process:` is now `archive:`, and `thumbnail`, `thumb_cols` and
  `thumb_rows` are now `sheet`, `sheet_columns` and `sheet_rows`. A file with the old names is
  rejected with the new name of each one.
- `status PATH...` checks archives before originals are deleted.
- `fingerprint` takes several videos.
