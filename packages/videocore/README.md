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
