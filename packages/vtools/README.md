# vtools

**One command for every tool of [video-tools](../../README.md).**

`vtools` installs [videothumb](../videothumb/README.md) (previews) and
[videosemanticreducer](../videosemanticreducer/README.md) (archives) together, and passes each
command to the tool that runs it. It only routes: every option, output and message is the one of
that tool. Installation: see the [main README](../../README.md#installation). The Windows zip of
each release has `vtools.exe` next to `vthumb.exe` and `vsr.exe`.

## Contents

- [Commands](#commands)
- [Quick start](#quick-start)
- [Options and help](#options-and-help)
- [Default settings](#default-settings)

## Commands

| Command | Runs | What it does |
|---|---|---|
| `vtools sheet` | `videothumb sheet` | A contact sheet per video |
| `vtools frames` | `videothumb frames` | A folder of full-size frames per video |
| `vtools pack` | `videothumb pack` | The contact sheet and the frames of each video in one ZIP |
| `vtools gui` | `videothumb gui` | The desktop app |
| `vtools archive` | `videosemanticreducer archive` | Archive videos before deleting them: summary video, contact sheet, report, fingerprint |
| `vtools fingerprint` | `videosemanticreducer fingerprint` | Only the fingerprint of videos |
| `vtools compare` | `videosemanticreducer compare` | Compare two fingerprint files |
| `vtools verify` | `videosemanticreducer verify` | Check a video against a saved fingerprint |
| `vtools status` | `videosemanticreducer status` | Check that archives are complete and unchanged, before deleting originals |
| `vtools config thumb\|vsr ...` | `videothumb config ...`, `videosemanticreducer config ...` | The defaults file of a tool |
| `vtools PATH...` | `videothumb PATH...` | The default task of videothumb: `sheet`, unless its config file sets `console.default_task` |

The names of earlier versions, `snapshots` and `process`, are passed on too, and each tool shows
the new command with a warning.

## Quick start

```bash
vtools movie.mp4                          # movie_sheet.jpg next to the video
vtools frames D:/Videos --count 20        # 20 full-size frames of every video in the folder
vtools pack D:/Videos -r -w 2             # one ZIP per video, subfolders included
vtools archive D:/Videos -o D:/Archive    # an archive of every video
vtools status D:/Videos -o D:/Archive --full   # can the originals be deleted?
vtools gui                                # the desktop app
```

## Options and help

```bash
vtools help                # every command, with examples
vtools help archive        # the options of a command; the same as `vtools archive --help`
vtools --version           # the version of vtools and of both tools
```

The options of each command are those of the tool that runs it, documented in its README:
[videothumb](../videothumb/README.md#commands),
[videosemanticreducer](../videosemanticreducer/README.md#commands). `-v/--verbose` and
`-q/--quiet` can also come before the command (`vtools -v archive ...`); they are passed after
it. A command name wins over a file with that name: write `.\sheet` for the file.

The tools keep their own commands, with short names: `vthumb` for `videothumb` and `vsr` for
`videosemanticreducer`.

## Default settings

Each tool has its own config file; `vtools config` reaches both:

```bash
vtools config thumb show                  # the defaults of videothumb and where they come from
vtools config thumb set sheet.columns 6
vtools config vsr set num_snapshots 100
vtools config vsr edit
```

See the default settings of [videothumb](../videothumb/README.md#default-settings) and
[videosemanticreducer](../videosemanticreducer/README.md#default-settings).
