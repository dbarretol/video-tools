"""Reusable tkinter/ttk widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

COLOR_SUCCESS = "#16A34A"
COLOR_ERROR = "#DC2626"
COLOR_WARNING = "#D97706"


class LabeledSpinbox(ttk.Frame):
    """A label followed by a spinbox that only accepts digits."""

    def __init__(
        self, parent: tk.Misc, label: str, *, from_: int = 1, to: int = 9999, initial: int = 1
    ) -> None:
        super().__init__(parent)
        self._var = tk.StringVar(value=str(initial))
        ttk.Label(self, text=label, width=22).pack(side="left", padx=(0, 8))
        validate = (self.register(lambda value: value == "" or value.isdigit()), "%P")
        ttk.Spinbox(
            self,
            from_=from_,
            to=to,
            textvariable=self._var,
            width=6,
            validate="key",
            validatecommand=validate,
        ).pack(side="left")

    def get(self) -> int:
        """The current value, or 0 when the field is empty."""
        value = self._var.get()
        return int(value) if value.isdigit() else 0
