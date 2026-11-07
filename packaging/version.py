"""Print the version of video-tools, after checking that every package has the same one."""

import sys
import tomllib
from pathlib import Path

packages = Path(__file__).resolve().parent.parent / "packages"
versions = {
    path.parent.name: tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"]
    for path in sorted(packages.glob("*/pyproject.toml"))
}
if len(set(versions.values())) != 1:
    sys.exit(f"The packages have different versions: {versions}")
print(versions["vtools"])
