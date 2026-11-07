# PyInstaller build of the three command lines, sharing one folder of libraries:
#   uv run --group build pyinstaller packaging/video-tools.spec
# Output: dist/video-tools/{vtools,vthumb,vsr}.exe and dist/video-tools/_internal/.
# The three executables are the same launcher; each runs the command line it is named after.
# ruff: noqa: F821  (Analysis, PYZ, EXE, COLLECT and SPECPATH are given by PyInstaller)

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

PROGRAMS = ["vtools", "vthumb", "vsr"]
PACKAGES = ["videocore", "videoconsole", "videothumb", "videosemanticreducer", "vtools"]

# imagehash only uses scipy.fftpack (with fft, linalg and special); the rest of scipy is
# referenced by its optional code paths and would add about 27 MB.
SCIPY_UNUSED = ["integrate", "interpolate", "ndimage", "optimize", "sparse", "spatial", "stats"]

analysis = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    # `--version` reads the installed metadata of each package.
    datas=[item for package in PACKAGES for item in copy_metadata(package)],
    # The launcher imports the command lines by name.
    hiddenimports=["vtools.cli", "videothumb.cli", "videosemanticreducer.cli"],
    excludes=["pytest", "setuptools", *(f"scipy.{name}" for name in SCIPY_UNUSED)],
)
pyz = PYZ(analysis.pure)
executables = [
    EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=name, console=True, upx=False)
    for name in PROGRAMS
]
COLLECT(*executables, analysis.binaries, analysis.datas, name="video-tools", upx=False)
