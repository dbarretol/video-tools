import tomllib
from pathlib import Path

import pytest

import videocore
import videothumb
from videothumb.cli import main


def declared_version() -> str:
    """The version in this package's pyproject.toml, which the installed one must match."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_version_is_exposed():
    assert videothumb.__version__ == declared_version()


def test_depends_on_videocore():
    assert videocore.__version__


def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"videothumb {declared_version()}" in capsys.readouterr().out
