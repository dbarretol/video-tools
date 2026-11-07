import tomllib
from pathlib import Path

import av
import PIL

import videocore


def declared_version() -> str:
    """The version in this package's pyproject.toml, which the installed one must match."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_version_is_exposed():
    assert videocore.__version__ == declared_version()


def test_runtime_dependencies_import():
    assert av.__version__
    assert PIL.__version__
