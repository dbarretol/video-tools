"""Entry point of the frozen executables: each one runs the command line it is named after."""

import multiprocessing
import sys
from pathlib import Path

PROGRAMS = {
    "vtools": "vtools.cli",
    "vthumb": "videothumb.cli",
    "vsr": "videosemanticreducer.cli",
}

if __name__ == "__main__":
    # Worker processes of a frozen program start this same executable; this makes them workers.
    multiprocessing.freeze_support()
    from importlib import import_module

    module = PROGRAMS.get(Path(sys.executable).stem.lower(), "vtools.cli")
    sys.exit(import_module(module).main())
