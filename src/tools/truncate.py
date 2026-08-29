from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TruncatedBy = Literal["lines", "bytes", None]


@dataclass(frozen=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: TruncatedBy
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    max_lines: int
    max_bytes: int
    partial_line: bool = False
    first_line_exceeds_limit: bool = False


DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


def _validate_limits(max_lines: int, max_bytes: int) -> None:
    if max_lines < 1:
        raise ValueError("max_lines must be at least 1")
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")


def _lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _result(
    content: str,
    *,
    truncated: bool,
    truncated_by: TruncatedBy,
    total_lines: int,
    total_bytes: int,
    max_lines: int,
    max_bytes: int,
    partial_line: bool = False,
    first_line_exceeds_limit: bool = False,
) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=truncated,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(_lines(content)),
        output_bytes=len(content.encode("utf-8")),
        max_lines=max_lines,
        max_bytes=max_bytes,
        partial_line=partial_line,
        first_line_exceeds_limit=first_line_exceeds_limit,
    )


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the beginning, never returning partial lines."""
    _validate_limits(max_lines, max_bytes)
    total_bytes = len(content.encode("utf-8"))
    lines = _lines(content)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _result(
            content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    if lines and len(lines[0].encode("utf-8")) > max_bytes:
        return _result(
            "",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
            first_line_exceeds_limit=True,
        )

    selected: list[str] = []
    used_bytes = 0
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = len(line.encode("utf-8")) + (1 if index else 0)
        if used_bytes + line_bytes > max_bytes:
            break
        selected.append(line)
        used_bytes += line_bytes

    by: Literal["lines", "bytes"] = (
        "lines" if len(selected) >= max_lines and used_bytes <= max_bytes else "bytes"
    )
    return _result(
        "\n".join(selected),
        truncated=True,
        truncated_by=by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _tail_bytes(line: str, max_bytes: int) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= max_bytes:
        return line
    start = max(0, len(raw) - max_bytes)
    while start < len(raw) and (raw[start] & 0xC0) == 0x80:
        start += 1
    return raw[start:].decode("utf-8", errors="replace")


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep the end, allowing a partial first returned line for one long line."""
    _validate_limits(max_lines, max_bytes)
    total_bytes = len(content.encode("utf-8"))
    lines = _lines(content)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _result(
            content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    selected: list[str] = []
    used_bytes = 0
    partial_line = False
    for line in reversed(lines):
        line_bytes = len(line.encode("utf-8")) + (1 if selected else 0)
        if used_bytes + line_bytes > max_bytes:
            if not selected:
                selected.insert(0, _tail_bytes(line, max_bytes))
                partial_line = True
            break
        selected.insert(0, line)
        used_bytes += line_bytes
        if len(selected) >= max_lines:
            break

    by: Literal["lines", "bytes"] = (
        "lines" if len(selected) >= max_lines and used_bytes <= max_bytes else "bytes"
    )
    return _result(
        "\n".join(selected),
        truncated=True,
        truncated_by=by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
        partial_line=partial_line,
    )


def truncation_notice(result: TruncationResult, direction: Literal["head", "tail"]) -> str:
    if not result.truncated:
        return ""
    side = "first" if direction == "head" else "last"
    if result.truncated_by == "lines":
        limit = f"{result.max_lines} line limit"
    else:
        limit = f"{result.max_bytes} byte limit"
    return (
        f"[Output truncated: showing {side} {result.output_lines} of "
        f"{result.total_lines} lines ({limit}).]"
    )
