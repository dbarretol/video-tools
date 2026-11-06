"""Console output shared by the video-tools command lines: live progress and run reports.

``videocore`` only emits progress events and returns reports; this package
draws them with rich. The desktop app consumes the same events on its own.
"""

from videoconsole.console import Console, setup_logging
from videoconsole.run import RunProgress, Wording, print_plan, print_report, report_run_lists

__all__ = [
    "Console",
    "RunProgress",
    "Wording",
    "print_plan",
    "print_report",
    "report_run_lists",
    "setup_logging",
]
