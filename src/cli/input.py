"""Minimal Pi-like single-line editor for the P0 TUI."""

from __future__ import annotations


class InputEditor:
    def __init__(self, history: list[str] | None = None) -> None:
        self.text = ""
        self.cursor = 0
        self.history = history or []
        self.history_index: int | None = None

    def clear(self) -> None:
        self.text = ""
        self.cursor = 0
        self.history_index = None

    def submit(self) -> str:
        value = self.text
        if value.strip():
            if not self.history or self.history[-1] != value:
                self.history.append(value)
        self.clear()
        return value

    def insert_text(self, value: str) -> None:
        """Insert a bracketed paste as one edit, including embedded newlines."""
        if not value:
            return
        self.text = self.text[: self.cursor] + value + self.text[self.cursor :]
        self.cursor += len(value)

    def handle(self, key: str) -> str | None:
        if key == "SHIFT_ENTER":
            self.text = self.text[: self.cursor] + "\n" + self.text[self.cursor :]
            self.cursor += 1
        elif key in {"\r", "\n"}:
            return self.submit()
        if key in {"\x08", "\x7f"}:
            if self.cursor:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
            return None
        if key == "LEFT":
            self.cursor = max(0, self.cursor - 1)
        elif key == "RIGHT":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "HOME" or key == "\x01":
            self.cursor = 0
        elif key == "END" or key == "\x05":
            self.cursor = len(self.text)
        elif key == "UP":
            self._history(-1)
        elif key == "DOWN":
            self._history(1)
        elif len(key) == 1 and key >= " " and key != "\x7f":
            self.text = self.text[: self.cursor] + key + self.text[self.cursor :]
            self.cursor += 1
        return None

    def _history(self, direction: int) -> None:
        if not self.history:
            return
        if self.history_index is None:
            self.history_index = len(self.history) if direction < 0 else -1
        self.history_index = max(-1, min(len(self.history) - 1, self.history_index + direction))
        self.text = "" if self.history_index < 0 else self.history[self.history_index]
        self.cursor = len(self.text)


__all__ = ["InputEditor"]
