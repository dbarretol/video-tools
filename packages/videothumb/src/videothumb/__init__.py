"""Contact sheets and snapshots from videos."""


def __getattr__(name: str) -> str:
    # The version is read from the installed metadata only when asked for:
    # importlib.metadata takes longer to import than most commands take to start.
    if name == "__version__":
        from importlib.metadata import version

        return version("videothumb")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
