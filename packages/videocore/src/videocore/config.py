"""Per-app YAML files of option defaults, and the ``config`` commands that manage them.

Each tool has one file, ``%APPDATA%/<app>/config.yaml`` on Windows
(``$XDG_CONFIG_HOME/<app>/config.yaml`` elsewhere), divided in sections. A
section holds the options of one or more option classes (see
``videocore.options``)::

    sheet:
      columns: 6
      jpeg_quality: 95

Values are applied in this order: command-line flags, then the config file,
then the built-in defaults. The file used is the one given with ``--config``,
else the one in ``$<APP>_CONFIG``, else the default one if it exists.

`AppConfig` reads and checks the file, renders a commented template and
changes single values in place (``config set``/``unset``), keeping the comments
and the order of the other lines. `add_config_commands` adds ``init``,
``show``, ``path``, ``edit``, ``set`` and ``unset`` to a command-line parser.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from videocore.errors import UsageError
from videocore.options import Option, options_of, parse_mapping
from videocore.outputs import atomic_write

Values = dict[str, dict[str, Any]]
"""Option values by section name, then by option name."""

_SECTION_LINE = re.compile(r"^(?P<name>[A-Za-z_][\w-]*):\s*(?:#.*)?$")
_ENTRY_LINE = re.compile(r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z_][\w-]*):(?P<rest>.*)$")
_COMMENT_START = re.compile(r"\s#")
_REMOVE = object()


class ConfigError(UsageError):
    """The config file is missing, unreadable or has invalid values."""


@dataclass(frozen=True)
class Section:
    """A part of the config file: the options of `classes`, except those in `skip`."""

    name: str
    classes: tuple[type, ...]
    comment: str | None = None
    """Shown next to the section name in the template."""
    skip: tuple[str, ...] = ()

    def options(self) -> list[Option]:
        seen = set(self.skip)
        result = []
        for cls in self.classes:
            for opt in options_of(cls):
                if opt.name not in seen:
                    seen.add(opt.name)
                    result.append(opt)
        return result

    def option(self, name: str) -> Option | None:
        return next((opt for opt in self.options() if opt.name == name), None)

    def effective(self, values: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Every option of the section: its value in `values`, else its built-in default."""
        values = values or {}
        return {opt.name: values.get(opt.name, opt.default) for opt in self.options()}

    def pick(self, cls: type, values: Mapping[str, Any]) -> dict[str, Any]:
        """The entries of `values` that are options of `cls`."""
        return {opt.name: values[opt.name] for opt in options_of(cls) if opt.name in values}


@dataclass(frozen=True)
class AppConfig:
    """The config file of one tool."""

    app: str
    sections: tuple[Section, ...]
    description: str
    """First line of the template, e.g. "videothumb defaults for the command line."."""

    @property
    def env_var(self) -> str:
        return self.app.upper() + "_CONFIG"

    def default_path(self) -> Path:
        """%APPDATA%/<app>/config.yaml on Windows, $XDG_CONFIG_HOME/<app>/config.yaml elsewhere."""
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / self.app / "config.yaml"

    def find(self, explicit: Path | None = None) -> Path | None:
        """The config file to read, or None when there is none. Explicit paths must exist."""
        if explicit is not None:
            if not explicit.is_file():
                raise ConfigError(f"Config file not found: {explicit}")
            return explicit
        if env := os.environ.get(self.env_var):
            path = Path(env)
            if not path.is_file():
                raise ConfigError(f"Config file not found: {path} (from {self.env_var})")
            return path
        path = self.default_path()
        return path if path.is_file() else None

    def target(self, explicit: Path | None = None) -> Path:
        """The file to create or change, whether it exists or not."""
        if explicit is not None:
            return explicit
        if env := os.environ.get(self.env_var):
            return Path(env)
        return self.default_path()

    def section(self, name: str) -> Section:
        return next(section for section in self.sections if section.name == name)

    def file_option(self) -> argparse.ArgumentParser:
        """A parent parser with ``--config FILE``, for every command that reads the file."""
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument(
            "--config",
            type=Path,
            metavar="FILE",
            help=f"YAML file with defaults (default: ${self.env_var} or {self.default_path()})",
        )
        return parent

    # Reading

    def load(self, path: Path | None) -> Values:
        """The values set in `path`, checked; every section is present, maybe empty."""
        if path is None:
            return {section.name: {} for section in self.sections}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read config file {path}: {exc}") from exc
        return self.parse(text, path)

    def parse(self, text: str, path: Path) -> Values:
        """Like `load`, from the text of `path`; relative paths are relative to `path`."""
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Could not read config file {path}: {exc}") from exc
        values: Values = {section.name: {} for section in self.sections}
        if data is None:
            return values
        names = [section.name for section in self.sections]
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: {self._expected_sections()}")
        if unknown := [key for key in data if key not in names]:
            expected = self._expected_sections()
            raise ConfigError(f"{path}: unknown section '{unknown[0]}'; {expected}")
        for section in self.sections:
            where = f"{path}: " if len(self.sections) == 1 else f"{path}: {section.name}: "
            raw = data.get(section.name) or {}
            if not isinstance(raw, dict):
                raise ConfigError(f"{where}'{section.name}' must be a mapping of option: value")
            try:
                parsed = parse_mapping(section.classes, raw, skip=section.skip)
                for cls in section.classes:  # checks that involve several options
                    cls(**section.pick(cls, parsed))
            except ValueError as exc:
                raise ConfigError(f"{where}{exc}") from None
            for name, value in parsed.items():
                if isinstance(value, Path) and not value.is_absolute():
                    parsed[name] = path.parent / value
            values[section.name] = parsed
        return values

    def _expected_sections(self) -> str:
        if len(self.sections) == 1:
            return f"expected a single '{self.sections[0].name}:' section"
        names = ", ".join(f"'{section.name}:'" for section in self.sections)
        return f"expected the sections {names}"

    # Writing

    def render(self, values: Mapping[str, Mapping[str, Any]] | None = None) -> str:
        """A commented YAML config with every option, set to `values` or its default."""
        values = values or {}
        lines = [
            f"# {self.description}",
            "# Command-line flags override these values; delete a line to use the built-in "
            "default.",
        ]
        for index, section in enumerate(self.sections):
            if index:
                lines.append("")
            header = f"{section.name}:"
            if section.comment:
                header += f"  # {section.comment}"
            lines.append(header)
            current = values.get(section.name, {})
            for opt in section.options():
                value = current.get(opt.name, opt.default)
                lines.append(f"  {opt.name}: {_dump(value)}  # {_describe(opt)}")
        return "\n".join(lines) + "\n"

    def resolve_key(self, key: str) -> tuple[Section, Option]:
        """The section and option named by `key`: ``section.option``, or ``option`` alone
        when a single section has it."""
        if "." in key:
            section_name, name = key.split(".", 1)
            section = next((s for s in self.sections if s.name == section_name), None)
            if section is None:
                valid = ", ".join(s.name for s in self.sections)
                raise ConfigError(f"Unknown section '{section_name}' (valid sections: {valid})")
            opt = section.option(name)
            if opt is None:
                valid = ", ".join(o.name for o in section.options())
                raise ConfigError(f"Unknown option '{name}' in {section_name} (valid: {valid})")
            return section, opt
        matches = [(s, opt) for s in self.sections if (opt := s.option(key)) is not None]
        if len(matches) == 1:
            return matches[0]
        if matches:
            keys = ", ".join(f"{s.name}.{key}" for s, _ in matches)
            raise ConfigError(f"'{key}' is in several sections; use one of {keys}")
        valid = ", ".join(self.keys())
        raise ConfigError(f"Unknown option '{key}' (valid options: {valid})")

    def keys(self) -> list[str]:
        """Every option as `set` accepts it."""
        if len(self.sections) == 1:
            return [opt.name for opt in self.sections[0].options()]
        return [f"{s.name}.{opt.name}" for s in self.sections for opt in s.options()]

    def set_value(self, path: Path, key: str, text: str) -> tuple[str, Any]:
        """Set `key` to `text` (YAML) in `path`, creating the file from the template if needed.

        Relative paths are taken as relative to the current folder and stored as
        absolute paths. Returns the full key and the value written. Nothing is
        written if the result would be invalid.
        """
        section, opt = self.resolve_key(key)
        value = _parse_value(opt, text)
        try:
            checked = parse_mapping(section.classes, {opt.name: value}, skip=section.skip)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        value = checked[opt.name]
        if isinstance(value, Path):
            value = value.resolve()
        self._rewrite(path, section, opt, value)
        return f"{section.name}.{opt.name}", value

    def unset_value(self, path: Path, key: str) -> tuple[str, bool]:
        """Remove `key` from `path`, so its built-in default applies.

        Returns the full key and whether it was set.
        """
        section, opt = self.resolve_key(key)
        full = f"{section.name}.{opt.name}"
        if not path.is_file():
            return full, False
        before = self.load(path)
        if opt.name not in before[section.name]:
            return full, False
        self._rewrite(path, section, opt, _REMOVE)
        return full, True

    def _rewrite(self, path: Path, section: Section, opt: Option, value: Any) -> None:
        """Set one entry of the file in place (`_REMOVE` removes it), keeping the other lines.

        When the file is not laid out as expected (e.g. YAML flow style), it is
        written again from the template, with the same values.
        """
        old_text = path.read_text(encoding="utf-8") if path.is_file() else self.render()
        try:
            expected = self.parse(old_text, path)
        except ConfigError:
            expected = None  # an invalid file can still be fixed by this change
        rendered = None if value is _REMOVE else _dump(value)
        new_text = _edit_entry(old_text, section.name, opt.name, rendered, _describe(opt))
        new_values = self.parse(new_text, path)  # raises if the change makes it invalid
        if expected is not None:
            if value is _REMOVE:
                expected[section.name].pop(opt.name, None)
            else:
                expected[section.name][opt.name] = value
            if new_values != expected:
                new_text = self.render(expected)
        with atomic_write(path) as part:
            part.write_text(new_text, encoding="utf-8", newline="\n")


def add_config_commands(
    parser: argparse.ArgumentParser,
    config: AppConfig,
    *,
    parents: Iterable[argparse.ArgumentParser] = (),
) -> None:
    """Add the ``init``, ``show``, ``path``, ``edit``, ``set`` and ``unset`` actions.

    Each action sets ``handler``: call ``args.handler(args)`` to run it; it
    returns the exit code and raises `ConfigError` on invalid input.
    """
    parents = [*parents, config.file_option()]
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)

    def add(name: str, help: str, handler: Any) -> argparse.ArgumentParser:
        action = actions.add_parser(name, parents=parents, help=help, description=help)
        action.set_defaults(handler=lambda args: handler(config, args))
        return action

    init = add("init", "write a commented config file with every option", _cmd_init)
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    add("show", "print the defaults in effect and their source", _cmd_show)
    add("path", "print where the config file is", _cmd_path)
    add("edit", "open the config file in an editor, creating it first if needed", _cmd_edit)
    keys = ", ".join(config.keys())
    set_action = add("set", "set the default of one option", _cmd_set)
    set_action.add_argument("key", metavar="KEY", help=f"one of: {keys}")
    set_action.add_argument("value", metavar="VALUE", help="the value; null for none")
    unset = add("unset", "go back to the built-in default of one option", _cmd_unset)
    unset.add_argument("key", metavar="KEY", help=f"one of: {keys}")


def _cmd_init(config: AppConfig, args: argparse.Namespace) -> int:
    path = config.target(args.config)
    if path.exists() and not args.force:
        raise ConfigError(f"{path} already exists; use --force to overwrite it")
    _create(config, path)
    print(f"Config file written to {path}")
    return 0


def _cmd_show(config: AppConfig, args: argparse.Namespace) -> int:
    path = config.find(args.config)
    values = config.load(path)  # before printing anything, so errors come alone
    print(f"# Source: {path or 'built-in defaults (no config file found)'}")
    print(config.render(values), end="")
    return 0


def _cmd_path(config: AppConfig, args: argparse.Namespace) -> int:
    path = config.target(args.config)
    status = "exists" if path.is_file() else "not created yet; run `config init`"
    print(f"{path} ({status})")
    return 0


def _cmd_edit(config: AppConfig, args: argparse.Namespace) -> int:
    path = config.target(args.config)
    if not path.exists():
        _create(config, path)
        print(f"Config file written to {path}")
    open_in_editor(path)
    print(f"Opened {path}; run `config show` afterwards to check it")
    return 0


def _cmd_set(config: AppConfig, args: argparse.Namespace) -> int:
    path = config.target(args.config)
    key, value = config.set_value(path, args.key, args.value)
    print(f"{key} = {_dump(value)} (in {path})")
    return 0


def _cmd_unset(config: AppConfig, args: argparse.Namespace) -> int:
    path = config.target(args.config)
    key, removed = config.unset_value(path, args.key)
    section, opt = config.resolve_key(key)
    default = opt.describe_default()
    if removed:
        print(f"{key} removed from {path}; the built-in default applies ({default})")
    else:
        print(f"{key} was not set; the built-in default applies ({default})")
    return 0


def _create(config: AppConfig, path: Path) -> None:
    with atomic_write(path) as part:
        part.write_text(config.render(), encoding="utf-8", newline="\n")


def open_in_editor(path: Path) -> None:
    """Open `path` with $VISUAL or $EDITOR, else the system's editor for text files."""
    if editor := os.environ.get("VISUAL") or os.environ.get("EDITOR"):
        subprocess.run([*shlex.split(editor, posix=os.name != "nt"), str(path)], check=False)
    elif sys.platform == "win32":
        try:
            os.startfile(path, "edit")  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["notepad.exe", str(path)])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-t", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _parse_value(opt: Option, text: str) -> Any:
    """`text` as YAML, except that text and path options take it literally."""
    if text.strip() in ("null", "~", ""):
        return None
    if opt.kind in (str, Path):
        return text
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text  # reported by the type check


def _dump(value: Any) -> str:
    if isinstance(value, Path):
        value = str(value)
    rendered = yaml.safe_dump(value, default_flow_style=True)
    return rendered.strip().removesuffix("...").strip()


def _describe(opt: Option) -> str:
    comment = opt.spec.help
    if opt.kind is Path:
        comment += "; relative paths are relative to this file"
    if opt.optional and opt.spec.default_text:
        comment += f"; null = {opt.spec.default_text}"
    return comment


def _edit_entry(text: str, section: str, key: str, value: str | None, comment: str) -> str:
    """`text` with the entry `key` of `section` set to YAML `value` (None removes it).

    An existing entry keeps its indentation and the comment after its value. A
    missing entry is added, with `comment`, at the end of its section; a missing
    section at the end of the file.
    """
    line = f"{key}: {value}  # {comment}"
    lines = text.splitlines()
    start = next((i for i, row in enumerate(lines) if _section_name(row) == section), None)
    if start is None:
        if value is None:
            return text
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"{section}:", f"  {line}"]
        return "\n".join(lines) + "\n"

    end = len(lines)
    for i in range(start + 1, len(lines)):
        row = lines[i]
        if row.strip() and not row[0].isspace() and not row.lstrip().startswith("#"):
            end = i
            break
    last_entry, indent = start, "  "
    for i in range(start + 1, end):
        match = _ENTRY_LINE.match(lines[i])
        if match is None:
            continue
        last_entry, indent = i, match["indent"]
        if match["key"] != key:
            continue
        if value is None:
            del lines[i]
        else:
            lines[i] = f"{indent}{key}: {value}{_trailing_comment(match['rest'])}"
        return "\n".join(lines) + "\n"
    if value is None:
        return text
    lines.insert(last_entry + 1, f"{indent}{line}")
    return "\n".join(lines) + "\n"


def _section_name(row: str) -> str | None:
    match = _SECTION_LINE.match(row)
    return match["name"] if match else None


def _trailing_comment(rest: str) -> str:
    """The comment after a YAML value, with the spaces before it; empty if there is none."""
    try:
        whole = yaml.safe_load(f"k:{rest}")
    except yaml.YAMLError:
        whole = None
    for match in _COMMENT_START.finditer(rest):
        try:
            if yaml.safe_load(f"k:{rest[: match.start()]}") == whole:
                start = match.start()
                while start > 0 and rest[start - 1].isspace():
                    start -= 1
                return rest[start:]
        except yaml.YAMLError:
            continue
    return ""
