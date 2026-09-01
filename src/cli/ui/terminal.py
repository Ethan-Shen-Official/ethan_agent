"""Cross-platform terminal capability boundary for the TUI."""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import time
import base64
from typing import TextIO


class TerminalBackend:
    """Raw-key terminal backend with a conservative non-TTY fallback."""

    def __init__(self, stdin=None, stdout: TextIO | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.is_tty = bool(self.stdin.isatty() and self.stdout.isatty())
        self._old_termios = None
        self._old_flags: int | None = None
        self._console_handle = None
        self._old_console_mode: int | None = None
        self._console_input_handle = None
        self._old_console_input_mode: int | None = None
        self._started = False

    @property
    def columns(self) -> int:
        return max(20, shutil.get_terminal_size(fallback=(80, 24)).columns)

    @property
    def rows(self) -> int:
        return max(8, shutil.get_terminal_size(fallback=(80, 24)).lines)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if not self.is_tty:
            return
        if os.name == "nt":
            self._enable_windows_vt()
        else:
            import termios
            import tty

            fd = self.stdin.fileno()
            self._old_termios = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        # Keep the interactive viewport in an alternate screen, as Pi does,
        # so full redraws never accumulate in normal terminal scrollback.
        self.write("\x1b[?1049h\x1b[?25l\x1b[?2004h")
        if self.is_tty:
            # Button-motion tracking is required for drag selection. SGR
            # encoding keeps coordinates unambiguous on modern terminals.
            self.write("\x1b[?1000h\x1b[?1002h\x1b[?1006h")

    def stop(self) -> None:
        if not self._started:
            return
        try:
            if self.is_tty and os.name != "nt" and self._old_termios is not None:
                import termios

                termios.tcsetattr(self.stdin.fileno(), termios.TCSADRAIN, self._old_termios)
            if self.is_tty and os.name == "nt":
                self._restore_windows_vt()
            if self.is_tty:
                self.write("\x1b[0m\x1b[?2004l\x1b[?1006l\x1b[?1002l\x1b[?1000l\x1b[?25h\x1b[?1049l\n")
        finally:
            self._started = False

    def _enable_windows_vt(self) -> None:
        """Enable ANSI output on legacy Windows consoles when possible."""
        try:
            import ctypes
            import msvcrt

            handle = ctypes.c_void_p(msvcrt.get_osfhandle(self.stdout.fileno()))
            mode = ctypes.c_uint32()
            kernel = ctypes.windll.kernel32
            if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                return
            self._console_handle = handle
            self._old_console_mode = mode.value
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel.SetConsoleMode(handle, mode.value | 0x0004)
            in_handle = ctypes.c_void_p(msvcrt.get_osfhandle(self.stdin.fileno()))
            in_mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(in_handle, ctypes.byref(in_mode)):
                self._console_input_handle = in_handle
                self._old_console_input_mode = in_mode.value
                # ENABLE_VIRTUAL_TERMINAL_INPUT makes Windows Terminal send
                # SGR mouse sequences through the same key stream.
                kernel.SetConsoleMode(in_handle, in_mode.value | 0x0200)
        except (AttributeError, OSError, ValueError):
            self._console_handle = None
            self._old_console_mode = None

    def _restore_windows_vt(self) -> None:
        if self._console_handle is None or self._old_console_mode is None:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleMode(self._console_handle, self._old_console_mode)
        except (AttributeError, OSError):
            pass
        finally:
            self._console_handle = None
            self._old_console_mode = None
        if self._console_input_handle is not None and self._old_console_input_mode is not None:
            try:
                import ctypes

                ctypes.windll.kernel32.SetConsoleMode(self._console_input_handle, self._old_console_input_mode)
            except (AttributeError, OSError):
                pass
            finally:
                self._console_input_handle = None
                self._old_console_input_mode = None

    def read_key(self, timeout: float = 0.05) -> str | None:
        if not self.is_tty:
            time.sleep(timeout)
            return None
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    first = msvcrt.getwch()
                    if first == "\x1b":
                        sequence = first
                        deadline = time.monotonic() + 0.03
                        while time.monotonic() < deadline and len(sequence) < 64:
                            if not msvcrt.kbhit():
                                time.sleep(0.001)
                                continue
                            sequence += msvcrt.getwch()
                            if sequence[-1].isalpha() or sequence[-1] == "~":
                                break
                        return self._decode_escape(sequence)
                    if first in {"\x00", "\xe0"}:
                        second = msvcrt.getwch()
                        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(second, second)
                    return first
                time.sleep(0.005)
            return None
        ready, _, _ = select.select([self.stdin], [], [], timeout)
        if not ready:
            return None
        value = os.read(self.stdin.fileno(), 1)
        if not value:
            return None
        first = value.decode("utf-8", errors="replace")
        if first != "\x1b":
            return first
        # Read the short CSI sequence used by arrows/home/end without
        # blocking the event loop when Escape is pressed by itself.
        sequence = first
        deadline = time.monotonic() + 0.03
        while time.monotonic() < deadline and len(sequence) < 64:
            ready, _, _ = select.select([self.stdin], [], [], 0.005)
            if not ready:
                break
            chunk = os.read(self.stdin.fileno(), 1)
            if not chunk:
                break
            sequence += chunk.decode("utf-8", errors="replace")
            if sequence[-1].isalpha() or sequence[-1] in {"~", "\r"}:
                break
        return self._decode_escape(sequence)

    @staticmethod
    def _decode_escape(sequence: str) -> str:
        if sequence.startswith("\x1b[<") and sequence[-1:] in {"M", "m"}:
            try:
                payload = sequence[3:-1]
                button_text, x_text, y_text = payload.split(";", 2)
                button = int(button_text)
                x = max(0, int(x_text) - 1)
                y = max(0, int(y_text) - 1)
            except (TypeError, ValueError):
                return "MOUSE_IGNORED"
            if button & 64:
                direction = "MOUSE_WHEEL_DOWN" if button & 1 else "MOUSE_WHEEL_UP"
                return direction
            # SGR uses a lowercase final byte for button release. Bit 32 is
            # motion while a button is held (used for drag selection).
            if sequence[-1:] == "m":
                return f"MOUSE_LEFT_UP:{x}:{y}"
            if button & 32:
                return f"MOUSE_LEFT_DRAG:{x}:{y}"
            if (button & 3) == 0:
                return f"MOUSE_LEFT_DOWN:{x}:{y}"
            return "MOUSE_IGNORED"
        return {
            "\x1b[A": "UP",
            "\x1b[B": "DOWN",
            "\x1b[C": "RIGHT",
            "\x1b[D": "LEFT",
            "\x1b[H": "HOME",
            "\x1b[F": "END",
            "\x1b[1~": "HOME",
            "\x1b[4~": "END",
            "\x1b[5~": "PAGEUP",
            "\x1b[6~": "PAGEDOWN",
            "\x1b[200~": "PASTE_START",
            "\x1b[201~": "PASTE_END",
            "\x1b[13;2u": "SHIFT_ENTER",
        }.get(sequence, "ESC")

    def write(self, text: str) -> None:
        encoding = getattr(self.stdout, "encoding", None)
        if encoding:
            # Legacy Windows code pages cannot represent Pi-style glyphs
            # (›/●/◌/box-drawing).  Degrade only those presentation glyphs;
            # user/model text is still encoded with the active console codec.
            if str(encoding).lower().replace("-", "") not in {"utf8", "utf8sig"}:
                text = text.translate(str.maketrans({
                    "›": ">", "●": "*", "◌": "o", "×": "x", "▌": "|",
                    "•": "*", "│": "|", "├": "+", "└": "+", "⊟": "-",
                    "─": "-",
                    "↑": "^", "↓": "v", "·": ".", "…": "...",
                    "⠋": "|", "⠙": "/", "⠹": "-", "⠸": "\\", "⠼": "|",
                    "⠴": "/", "⠦": "-", "⠧": "\\", "⠇": "|", "⠏": "/",
                }))
            text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        self.stdout.write(text)
        self.stdout.flush()

    def clear(self) -> None:
        if self.is_tty:
            self.write("\x1b[2J\x1b[H")

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy text through a verified native helper, with OSC 52 fallback.

        OSC 52 is useful in remote/tmux sessions but cannot reliably report
        whether the host accepted the clipboard write, so native helpers are
        preferred when available.
        """
        if not text:
            return False
        try:
            if os.name == "nt" and shutil.which("clip"):
                completed = subprocess.run(
                    ["clip"],
                    input=text.encode("utf-16le"),
                    text=False,
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
                if completed.returncode == 0:
                    return True
            for command in (("wl-copy",), ("xclip", "-selection", "clipboard")):
                if shutil.which(command[0]):
                    completed = subprocess.run(
                        command,
                        input=text.encode("utf-8"),
                        text=False,
                        capture_output=True,
                        timeout=2,
                        check=False,
                    )
                    if completed.returncode == 0:
                        return True
        except (OSError, subprocess.SubprocessError):
            pass
        if self.is_tty:
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            self.write(f"\x1b]52;c;{encoded}\x07")
            return True
        return False


__all__ = ["TerminalBackend"]
