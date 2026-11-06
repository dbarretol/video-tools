"""Tool options declared once, as dataclass fields with metadata.

Each option is a field created with `option`, which records its help text, flags,
limits and how a form should present it. Everything else is derived from that
single declaration:

- `validate` checks an instance (call it from ``__post_init__``);
- `add_arguments` and `given_values` build and read the argparse flags;
- `parse_mapping` reads and checks values from a config file;
- `options_of` lists the options with their types, for templates and forms.

Fields declared with `dataclasses.field` instead of `option` are internal and
are not exposed anywhere.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

METADATA_KEY = "videocore.option"
_KINDS = (bool, int, float, str, Path)


@dataclass(frozen=True)
class Spec:
    """How an option is described, checked and presented."""

    help: str
    flags: tuple[str, ...] = ()
    """Command-line flags; ``--field-name`` when empty."""
    gt: float | None = None
    ge: float | None = None
    le: float | None = None
    label: str | None = None
    """Short name for forms; derived from the field name when missing."""
    group: str | None = None
    """Section of the help and of forms."""
    advanced: bool = False
    """Hidden behind an "advanced" section in forms."""
    cli: bool = True
    """False for options that only exist in config files and forms."""
    default_text: str | None = None
    """Shown instead of the default value, e.g. "automatic" for None."""


def option(default: Any, help: str, **spec: Any) -> Any:
    """A dataclass field that is a user-facing option; see `Spec` for the arguments."""
    return dataclasses.field(default=default, metadata={METADATA_KEY: Spec(help, **spec)})


@dataclass(frozen=True)
class Option:
    """An option of a dataclass, with its type resolved."""

    name: str
    kind: type
    """bool, int, float, str or Path."""
    optional: bool
    """None is a valid value."""
    choices: tuple[Any, ...] | None
    default: Any
    spec: Spec

    @property
    def label(self) -> str:
        return self.spec.label or self.name.replace("_", " ").capitalize()

    @property
    def flags(self) -> tuple[str, ...]:
        return self.spec.flags or ("--" + self.name.replace("_", "-"),)

    def describe_default(self) -> str:
        if self.spec.default_text is not None:
            return self.spec.default_text
        if self.kind is bool:
            return "on" if self.default else "off"
        return "none" if self.default is None else str(self.default)

    def limits(self) -> str | None:
        """The allowed range in words, e.g. "between 1 and 100"; None when unlimited."""
        spec = self.spec
        if spec.ge is not None and spec.le is not None:
            return f"between {_number(spec.ge)} and {_number(spec.le)}"
        parts = []
        if spec.gt is not None:
            parts.append(f"greater than {_number(spec.gt)}")
        if spec.ge is not None:
            parts.append(f"at least {_number(spec.ge)}")
        if spec.le is not None:
            parts.append(f"at most {_number(spec.le)}")
        return " and ".join(parts) or None

    def check(self, value: Any) -> None:
        """Raise ValueError when `value` has the wrong type or is out of range."""
        if value is None:
            if not self.optional:
                raise ValueError(f"{self.name} must be set")
            return
        if not _has_kind(value, self.kind):
            raise ValueError(f"{self.name} must be {self.type_name()}, got {value!r}")
        if self.choices is not None and value not in self.choices:
            valid = ", ".join(map(str, self.choices))
            raise ValueError(f"{self.name} must be one of {valid}, got {value!r}")
        spec = self.spec
        if (
            (spec.gt is not None and not value > spec.gt)
            or (spec.ge is not None and not value >= spec.ge)
            or (spec.le is not None and not value <= spec.le)
        ):
            raise ValueError(f"{self.name} must be {self.limits()}, got {_number(value)}")

    def type_name(self) -> str:
        name = {Path: "a path", str: "text"}.get(self.kind, self.kind.__name__)
        return f"{name} or null" if self.optional else name

    def parse_text(self, text: str) -> Any:
        """Convert a command-line value; raise ValueError with a short message."""
        if self.kind is int:
            try:
                value = int(text)
            except ValueError:
                raise ValueError(f"expected a whole number, got {text!r}") from None
        elif self.kind is float:
            try:
                value = float(text)
            except ValueError:
                raise ValueError(f"expected a number, got {text!r}") from None
        else:
            value = self.kind(text)
        try:
            self.check(value)
        except ValueError as exc:
            raise ValueError(str(exc).removeprefix(f"{self.name} ")) from None
        return value


@functools.cache
def options_of(cls: type) -> tuple[Option, ...]:
    """The options of dataclass `cls`, in declaration order."""
    hints = typing.get_type_hints(cls)
    result = []
    for field in dataclasses.fields(cls):
        spec = field.metadata.get(METADATA_KEY)
        if spec is None:
            continue
        kind, optional, choices = _resolve(hints[field.name], f"{cls.__name__}.{field.name}")
        result.append(Option(field.name, kind, optional, choices, field.default, spec))
    return tuple(result)


def validate(obj: Any) -> None:
    """Check every option of dataclass instance `obj`; raise ValueError on the first error."""
    for opt in options_of(type(obj)):
        opt.check(getattr(obj, opt.name))


def add_arguments(
    parser: argparse.ArgumentParser, *classes: type, skip: Iterable[str] = ()
) -> None:
    """Add a flag for each command-line option of `classes`.

    Every flag defaults to None, so `given_values` can tell the flags given from
    those left out, and values from a config file or the dataclass defaults
    apply to the rest. Options in a group go to an argument group of that name.
    An option shared by several classes (same name) is added once.
    """
    seen = set(skip)
    groups: dict[str, argparse._ArgumentGroup] = {}
    for cls in classes:
        for opt in options_of(cls):
            if opt.name in seen or not opt.spec.cli:
                continue
            seen.add(opt.name)
            target: Any = parser
            if opt.spec.group is not None:
                if opt.spec.group not in groups:
                    groups[opt.spec.group] = parser.add_argument_group(opt.spec.group)
                target = groups[opt.spec.group]
            help_text = f"{opt.spec.help} (default: {opt.describe_default()})"
            if opt.kind is bool:
                target.add_argument(
                    *opt.flags,
                    dest=opt.name,
                    action=argparse.BooleanOptionalAction,
                    default=None,
                    help=help_text,
                )
            else:
                target.add_argument(
                    *opt.flags,
                    dest=opt.name,
                    type=_argparse_type(opt),
                    choices=opt.choices,
                    default=None,
                    metavar=None if opt.choices else _metavar(opt),
                    help=help_text,
                )


def given_values(cls: type, args: argparse.Namespace) -> dict[str, Any]:
    """The options of `cls` given on the command line, by field name."""
    return {
        opt.name: getattr(args, opt.name)
        for opt in options_of(cls)
        if getattr(args, opt.name, None) is not None
    }


def parse_mapping(classes: type | Iterable[type], data: Mapping[str, Any]) -> dict[str, Any]:
    """Check config-file values against the options of `classes`.

    Returns the values by field name, with paths as `Path` and whole numbers as
    float where a float is expected. Raises ValueError naming the first unknown
    key or invalid value.
    """
    if isinstance(classes, type):
        classes = [classes]
    known = {opt.name: opt for cls in classes for opt in options_of(cls)}
    values = {}
    for key, value in data.items():
        opt = known.get(key)
        if opt is None:
            raise ValueError(f"unknown option '{key}' (valid options: {', '.join(known)})")
        if opt.kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        elif opt.kind is Path and isinstance(value, str):
            value = Path(value).expanduser()
        try:
            opt.check(value)
        except ValueError as exc:
            raise ValueError(f"'{key}' {str(exc).removeprefix(f'{key} ')}") from None
        values[key] = value
    return values


def _resolve(hint: Any, where: str) -> tuple[type, bool, tuple[Any, ...] | None]:
    """(kind, optional, choices) of a type hint such as ``int | None`` or a Literal."""
    optional = False
    args = typing.get_args(hint)
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        optional = type(None) in args
        rest = [a for a in args if a is not type(None)]
        if len(rest) != 1:
            raise TypeError(f"{where}: unsupported option type {hint!r}")
        hint = rest[0]
    if typing.get_origin(hint) is typing.Literal:
        choices = typing.get_args(hint)
        return type(choices[0]), optional, choices
    if hint not in _KINDS:
        raise TypeError(f"{where}: unsupported option type {hint!r}")
    return hint, optional, None


def _has_kind(value: Any, kind: type) -> bool:
    # bool is a subclass of int: `true` is not a number.
    if isinstance(value, bool):
        return kind is bool
    if kind is float:
        return isinstance(value, (int, float))
    return isinstance(value, kind)


def _argparse_type(opt: Option):
    def parse(text: str) -> Any:
        try:
            return opt.parse_text(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None

    parse.__name__ = opt.kind.__name__  # argparse names the type in some messages
    return parse


def _metavar(opt: Option) -> str:
    return {int: "N", float: "SECONDS" if "second" in opt.spec.help else "X", Path: "PATH"}.get(
        opt.kind, opt.name.upper()
    )


def _number(value: float) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
