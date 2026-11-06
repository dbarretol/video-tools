# videocore

Shared library used by `videothumb` and `videosemanticreducer`: video probing, frame sampling,
grid composition, parallel batch processing, output planning, atomic writes, config files and
state between runs. Not intended to be
used on its own.

## Frames

`sample_frames`/`iter_frames` take frames at exact positions (`mode="precise"`) or at the
keyframe before each position (`mode="keyframe"`, several times faster). With `fallback=True`,
keyframe mode takes precisely the positions where it would repeat the previous keyframe or got no
frame, so every position shows a distinct frame.

## Options

A tool declares each of its options once, as a dataclass field created with
`videocore.options.option`:

```python
@dataclass(frozen=True)
class SheetOptions:
    columns: int = option(5, "thumbnails per row", gt=0, group="contact sheet")

    def __post_init__(self) -> None:
        validate(self)
```

From that declaration, `add_arguments` builds the command-line flags (type, limits and default in
the help, `--flag/--no-flag` for booleans), `given_values` reads the flags given, `parse_mapping`
checks config-file values and `options_of` lists the options for templates and forms. Fields made
with `dataclasses.field` are internal and are not exposed.

## Config files

`videocore.config.AppConfig` describes the YAML file of defaults of one tool: its sections, each
with the options of one or more option classes. It finds the file (`--config`, then
`$<APP>_CONFIG`, then `%APPDATA%\<app>\config.yaml`), reads and checks it, renders a commented
template and changes single values in place, keeping comments. `add_config_commands` adds the
`init`, `show`, `path`, `edit`, `set` and `unset` actions to a command-line parser.

## State

`videocore.State` is what a tool remembers between runs, in `%LOCALAPPDATA%\<app>\state.json`.
It is not meant to be edited; a missing or damaged file reads as empty. Today it keeps how much
longer than its estimate each task takes on this computer: pass `speed=state.speed(task.name)` to
`plan` and call `state.learn_speed(task.name, report.measured_speed())` after `execute`.

## Inputs

`videocore.collect_inputs(paths, input_lists, recursive=, skip_dir=)` joins the videos given as
paths (used as they are when they exist, else expanded as patterns), folders and input lists
(`read_input_list`: one path per line, `#` comments, quotes removed, UTF-8 with or without BOM,
`-` for standard input) into one `Inputs`, without duplicates and in order. `Inputs.roots` says
which folder each video came from, so `plan(source=inputs.roots)` recreates its subfolders inside
an output folder, and `Inputs.invalid` (listed paths that do not exist) goes to
`plan(invalid=...)`, which plans them as FAILED. `add_input_arguments` adds `PATH...` and
`--input-list` to an argparse parser.

## Jobs

`videocore.jobs` is the contract between a tool and whatever runs it (a CLI today, a GUI later).
A tool implements a `Task`: where the output of a video goes, whether an existing output is still
good (`SKIPPED`) or was made with other options (`OUTDATED`), how to process one video and how to
clean up after a killed one. Then:

```python
planned = plan(task, videos, options, RunOptions(output=out, workers=4), source=folder)
for line in planned.summary():  # counts, length of video, rough ETA, cloud-only files
    print(line)
report = execute(planned, on_progress=..., should_cancel=...)
report.items  # one ItemResult per video, in order: OK, FAILED, SKIPPED, OUTDATED or CANCELLED
```

After processing a video, `execute` calls `Task.verify`, which reads the outputs back with the
helpers of `videocore.verify`: a missing or unreadable output fails the video, an incomplete one
(fewer than 90% of its frames decoded) makes it DEGRADED. `report.exit_status(strict=...)` gives
0, 1 or 3.

On Ctrl+C, `execute` returns the report with `interrupted=True` and the unfinished videos as
CANCELLED instead of raising. `write_run_lists(report, default_runs_dir(app), title=...)` writes
the failed and the unfinished videos to text files, one path per line.

`plan` writes nothing, so it also serves `--dry-run`. `RunOptions` holds how to run (output root,
recursion, workers, overwrite); each task keeps its own options for what to produce. When several
videos run in parallel, `execute` gives each one a single decoder.

## Progress and cancellation

Inside a task, `videocore.progress.stage("snapshots", done, total)` reports where a video is and
`progress.check_cancelled()` stops it with `Cancelled` when the run was asked to stop. Both do
nothing outside a run. `sample_frames`/`iter_frames` take `stage=` to report every decoded frame,
and they check for cancellation at every frame.

`execute(..., on_event=...)` receives those events (`ProgressEvent`: item, stage, done, total) from
every video, including those running in worker processes. `should_cancel` can be a `CancelToken`.
On cancel or Ctrl+C, running videos stop at their next frame and clean up their unfinished files;
a process that does not stop within a few seconds is killed.
