from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class ExecutionEnv:
    """Capability boundary for local file and process operations."""

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def edit_file(
        self, path: str, old_text: str, new_text: str, replace_all: bool = False
    ) -> int:
        raise NotImplementedError

    def list_dir(
        self,
        path: str = ".",
        depth: int = 1,
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> list[str]:
        raise NotImplementedError

    def search(
        self, pattern: str, max_results: int = 200, include_hidden: bool = False
    ) -> list[str]:
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
        self._atomic_write_text(target, content)

    def edit_file(
        self, path: str, old_text: str, new_text: str, replace_all: bool = False
    ) -> int:
        if not old_text:
            raise ValueError("old_text must not be empty")
        target = self._path(path)
        content = target.read_text(encoding="utf-8")
        matches = content.count(old_text)
        if matches == 0:
            raise ValueError(f"text not found in {path}")
        if matches > 1 and not replace_all:
            raise ValueError(
                f"old_text matched {matches} locations in {path}; "
                "provide a unique snippet or set replace_all=true"
            )
        updated = content.replace(old_text, new_text, -1 if replace_all else 1)
        self._atomic_write_text(target, updated)
        return matches if replace_all else 1

    def list_dir(
        self,
        path: str = ".",
        depth: int = 1,
        max_entries: int = 200,
        include_hidden: bool = False,
    ) -> list[str]:
        if depth < 1:
            raise ValueError("depth must be at least 1")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        root = self._path(path)
        if not root.is_dir():
            raise NotADirectoryError(path)

        results: list[str] = []

        def visit(directory: Path, level: int) -> None:
            if len(results) >= max_entries:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                return
            for entry in entries:
                if len(results) >= max_entries:
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
        self, pattern: str, max_results: int = 200, include_hidden: bool = False
    ) -> list[str]:
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        results: list[str] = []
        for path in sorted(self.cwd.rglob(pattern), key=lambda item: str(item).lower()):
            if path.is_symlink():
                continue
            relative = path.resolve().relative_to(self.cwd)
            if not include_hidden and any(part.startswith(".") for part in relative.parts):
                continue
            results.append(str(relative))
            if len(results) >= max_results:
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
