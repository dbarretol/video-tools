# videoconsole

Terminal output shared by the command lines of video-tools, drawn with
[rich](https://github.com/Textualize/rich). `videocore` only plans runs, emits progress events and
returns reports; this package shows them. The desktop app of videothumb consumes the same events
on its own and does not use it.

- `Console`: where a command writes. Results go to stdout; warnings, errors, hints and the live
  progress go to stderr. Text is printed literally (file names such as `clip [1080p].mp4` are not
  read as rich markup) and long lines are not wrapped. `quiet` (`-q`) keeps only what needs
  attention, and `progress=False` (`--no-progress`) leaves one line per video instead of the live
  progress.
- `setup_logging(app, console, verbose=)`: logs to the tool's file and, only with `verbose`
  (`-v`), to the console, above the progress bars.
- `print_plan(console, plan, wording)`: the plan summary before a run, and with `items=True` one
  line per video (`--dry-run`).
- `RunProgress(console, plan, wording)`: the `on_progress`/`on_event` callbacks of
  `videocore.execute`. In a terminal it draws a bar for the batch, weighted by the length of each
  video so the time remaining is meaningful, and one row per running video with its stage; each
  finished video leaves a `[3/10] name: OK` line. Without a terminal, only those lines.
- `print_report(console, report, wording, log_file=)`: the counts of the run, then each failed or
  incomplete video with its reason and hint, and where the log is.
- `report_run_lists(...)`: writes the lists of videos to retry or resume
  (`videocore.write_run_lists`) and says where they are and how to resume.

With `Console(json=True, tool=...)`, stdout is kept for one JSON document and every message goes
to stderr. The functions above add to it (`plan`, `report`, `run_lists`), a command adds its own
fields with `console.record(...)` and an error with `console.record_error(exc, hint)`, and
`console.finish(exit_code, log_file=)` writes it (ASCII, `schema` = `JSON_SCHEMA`) and returns the
exit code. Without `json`, `record` and `finish` print nothing. `plan_document` and
`report_document` give the same data as dictionaries.

`Desktop(app, console, wording, open_output=)` is the Windows side of a run (no-ops elsewhere):
`with desktop.running(plan):` around `execute` keeps the computer awake (`keep_awake`,
`SetThreadExecutionState`) and times the run; `desktop.finish(exit_code)` shows a notification
when it took `notify_after` minutes or more (`notify`: a toast with the optional
`videoconsole[notify]` extra, else the console window flashes), opens the output folder with
`open_output`, and when `launched_alone()` (no shell attached to the console: started from the
Explorer) waits for Enter, offering to open the folder. `ConsoleOptions` is the `console` section
of a tool's config file; `pause_if_alone()` keeps the window open on invalid arguments.

`Wording` is how a tool names what it does ("archived" rather than "succeeded") and its stages.

```python
from videoconsole import Console, RunProgress, Wording, print_plan, print_report, setup_logging
from videocore import execute, plan

console = Console(quiet=args.quiet)
log_file = setup_logging("mytool", console, verbose=args.verbose)
planned = plan(TASK, videos, options, run, source=folder)
print_plan(console, planned, WORDING)
with RunProgress(console, planned, WORDING) as progress:
    report = execute(planned, on_progress=progress.on_progress, on_event=progress.on_event)
print_report(console, report, WORDING, log_file=log_file)
```
