from __future__ import annotations

import os
import subprocess
from pathlib import Path


class ExecutionEnv:
    """Capability boundary for local file and process operations."""

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def search(self, pattern: str) -> list[str]:
        raise NotImplementedError

    def execute(self, command: str) -> tuple[int, str, str]:
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def search(self, pattern: str) -> list[str]:
        results: list[str] = []
        for path in self.cwd.rglob(pattern):
            try:
                results.append(str(path.resolve().relative_to(self.cwd)))
            except ValueError:
                continue
        return results

    def execute(self, command: str) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                command,
                cwd=self.cwd,
                shell=True,
                text=True,
                capture_output=True,
                timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            return 124, stdout, f"command timed out after {self.command_timeout:g}s\n{stderr}"
        return completed.returncode, self._limit_output(completed.stdout), self._limit_output(completed.stderr)

    def _limit_output(self, output: str) -> str:
        encoded = output.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_output_bytes:
            return output
        truncated = encoded[: self.max_output_bytes].decode("utf-8", errors="replace")
        return f"{truncated}\n...[output truncated at {self.max_output_bytes} bytes]"
