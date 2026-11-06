# videocore

Shared library used by `videothumb` and `videosemanticreducer`: video probing, frame sampling,
grid composition, parallel batch processing, output planning and atomic writes. Not intended to be
used on its own.

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

`plan` writes nothing, so it also serves `--dry-run`. `RunOptions` holds how to run (output root,
recursion, workers, overwrite); each task keeps its own options for what to produce. When several
videos run in parallel, `execute` gives each one a single decoder.
