import pytest

import videocore
import videothumb
from videothumb.cli import main


def test_version_is_exposed():
    assert videothumb.__version__ == "0.1.0"


def test_depends_on_videocore():
    assert videocore.__version__


def test_cli_without_arguments_exits_cleanly():
    assert main([]) == 0


def test_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "videothumb 0.1.0" in capsys.readouterr().out
