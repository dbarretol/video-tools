"""Command-line entry point for videothumb."""

from __future__ import annotations

import argparse

from videothumb import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="videothumb",
        description="Generate contact sheets and individual snapshots from videos.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
