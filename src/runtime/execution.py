from __future__ import annotations

import locale
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .permissions import is_destructive_shell_command, is_protected_shell_command


class ExecutionEnv:
    """Capability boundary for local file and process operations."""

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def list_dir(
        self,
        path: str = ".",
        depth: int = 1,
        limit: int = 200,
        include_hidden: bool = False,
    ) -> list[str]:
        raise NotImplementedError

    def search(
        self, pattern: str, limit: int = 200, include_hidden: bool = False
    ) -> list[str]:
        raise NotImplementedError

    def execute(
        self,
        command: str,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        on_output: Callable[[dict[str, str]], None] | None = None,
    ) -> tuple[int, str, str]:
        raise NotImplementedError


class LocalExecutionEnv(ExecutionEnv):
    def __init__(
        self,
        cwd: str | os.PathLike[str] = ".",
        command_timeout: float = 120.0,
        max_output_bytes: int = 200_000,
    ) -> None:
        self.cwd = Path(cwd).resolve()
        if not self.cwd.is_dir():
            raise ValueError(f"Workspace does not exist or is not a directory: {self.cwd}")
        self.command_timeout = command_timeout
        self.max_output_bytes = max_output_bytes

    def _path(self, path: str) -> Path:
        candidate = (self.cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            candidate.relative_to(self.cwd)
        except ValueError as exc:
            raise PermissionError(f"Path is outside workspace: {path}") from exc
        return candidate

    def read_file(self, path: str) -> str:
        return self._path(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._path(path)
        self._assert_mutable_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_text(target, content)

    def list_dir(
        self,
        path: str = ".",
        depth: int = 1,
        limit: int = 200,
        include_hidden: bool = False,
    ) -> list[str]:
        if depth < 1:
            raise ValueError("depth must be at least 1")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        root = self._path(path)
        if not root.is_dir():
            raise NotADirectoryError(path)

        results: list[str] = []

        def visit(directory: Path, level: int) -> None:
            if len(results) >= limit:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                return
            for entry in entries:
                if len(results) >= limit:
                    return
                if not include_hidden and entry.name.startswith("."):
                    continue
                relative = entry.relative_to(self.cwd)
                display = str(relative)
                if entry.is_dir() and not entry.is_symlink():
                    display += os.sep
                results.append(display)
                if entry.is_dir() and not entry.is_symlink() and level < depth:
                    visit(entry, level + 1)

        visit(root, 1)
        return results

    def search(
        self, pattern: str, limit: int = 200, include_hidden: bool = False
    ) -> list[str]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        results: list[str] = []
        for path in sorted(self.cwd.rglob(pattern), key=lambda item: str(item).lower()):
            if path.is_symlink():
                continue
            relative = path.resolve().relative_to(self.cwd)
            if not include_hidden and any(part.startswith(".") for part in relative.parts):
                continue
            results.append(str(relative))
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                delete=False,
            ) as handle:
                temp_path = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass

    def _assert_mutable_path(self, path: str | os.PathLike[str]) -> None:
        """Keep runtime and repository metadata immutable for tool writes."""
        normalized = str(path).replace("\\", "/").strip("/").lower()
        parts = {part for part in normalized.split("/") if part not in {"", ".", ".."}}
        if parts & {".agent", ".git"}:
            raise PermissionError("protected workspace metadata cannot be modified")

    def execute(
        self,
        command: str,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        on_output: Callable[[dict[str, str]], None] | None = None,
    ) -> tuple[int, str, str]:
        # This check is deliberately below the permission-policy layer. It is
        # the final capability boundary before the process starts.
        if is_destructive_shell_command(command):
            raise PermissionError("destructive workspace-wide command is blocked")
        if is_protected_shell_command(command):
            raise PermissionError("protected workspace metadata cannot be modified")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            command,
            cwd=self.cwd,
            shell=True,
            # Commands inherit the user's Windows code page (usually GBK),
            # but compilers and binary-oriented tools can emit arbitrary
            # bytes.  Replacement decoding keeps those diagnostics visible
            # without allowing one malformed byte to crash the TUI reader
            # thread with UnicodeDecodeError.
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        effective_timeout = self.command_timeout if timeout is None else float(timeout)
        if effective_timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        deadline = time.monotonic() + effective_timeout
        cancelled = False

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        def append_bounded(target: list[str], chunk: str) -> None:
            target.append(chunk)
            # Keep a small rolling tail so an unbounded command cannot consume
            # process memory before ToolExecutor applies its own limits.
            if sum(len(part.encode("utf-8", errors="replace")) for part in target) > self.max_output_bytes * 2:
                encoded = "".join(target).encode("utf-8", errors="replace")[-self.max_output_bytes * 2 :]
                target[:] = [encoded.decode("utf-8", errors="replace")]

        def pump(stream, target: list[str], name: str) -> None:
            if stream is None:
                return
            try:
                for chunk in iter(lambda: stream.read(4096), ""):
                    if not chunk:
                        break
                    append_bounded(target, chunk)
                    if on_output is not None:
                        try:
                            on_output({"stream": name, "text": chunk})
                        except Exception:
                            # Rendering callbacks must never break a process.
                            pass
            except (OSError, ValueError):
                return

        readers = [
            threading.Thread(target=pump, args=(process.stdout, stdout_parts, "stdout"), daemon=True),
            threading.Thread(target=pump, args=(process.stderr, stderr_parts, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                self._terminate_process(process)
                break
            if time.monotonic() >= deadline:
                self._terminate_process(process)
                for reader in readers:
                    reader.join(timeout=0.5)
                stderr = f"command timed out after {effective_timeout:g}s\n{''.join(stderr_parts)}"
                return 124, self._limit_output("".join(stdout_parts), from_end=True), self._limit_output(stderr, from_end=True)
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                continue
        for reader in readers:
            reader.join(timeout=0.5)
        stdout, stderr = "".join(stdout_parts), "".join(stderr_parts)
        if cancelled:
            stderr = f"command cancelled\n{stderr or ''}"
            return 130, self._limit_output(stdout or "", from_end=True), self._limit_output(stderr, from_end=True)
        # Preserve the end of process output so ToolExecutor can apply its
        # user-facing tail truncation without losing the final diagnostics.
        return (
            process.returncode if process.returncode is not None else 1,
            self._limit_output(stdout or "", from_end=True),
            self._limit_output(stderr or "", from_end=True),
        )

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _communicate_after_stop(process: subprocess.Popen) -> tuple[str, str]:
        """Collect output briefly, then avoid waiting on an escaped child."""
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            return stdout or "", stderr or ""
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        return "", "process did not close its output pipes after termination"
    def _limit_output(self, output: str, *, from_end: bool = False) -> str:
        encoded = output.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_output_bytes:
            return output
        if from_end:
            truncated = encoded[-self.max_output_bytes :]
            while truncated and (truncated[0] & 0xC0) == 0x80:
                truncated = truncated[1:]
        else:
            truncated = encoded[: self.max_output_bytes]
        text = truncated.decode("utf-8", errors="replace")
        side = "end" if from_end else "start"
        return f"{text}\n...[output truncated at {self.max_output_bytes} bytes from {side}]"
