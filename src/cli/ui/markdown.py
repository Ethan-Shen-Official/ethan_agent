"""Small dependency-free Markdown renderer for transcript components.

It intentionally covers the syntax most useful in agent replies while
keeping terminal width accounting deterministic: headings, lists, quotes,
fenced code and inline emphasis/code are normalized into display lines.
"""

from __future__ import annotations

import re
import unicodedata


_INLINE = re.compile(r"(\*\*|__)(.+?)\1|(`+)(.*?)\3|(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)")
_ESCAPED_MARKUP = re.compile(r"\\([\\`*_{}\[\]()#+.!|>~-])")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def render_markdown(text: str, width: int) -> list[str]:
    width = max(1, int(width))
    result: list[str] = []
    in_fence = False
    cleaned = _CONTROL.sub("", _ANSI.sub("", _OSC.sub("", str(text or "").replace("\r", ""))))
    for raw in cleaned.split("\n"):
        line = _ESCAPED_MARKUP.sub(r"\1", raw.expandtabs(4))
        fence = line.strip().startswith("```") or line.strip().startswith("~~~")
        if fence:
            in_fence = not in_fence
            continue
        if in_fence:
            logical = "  " + line
        else:
            logical = _normalize_inline(_normalize_block(line))
        if not logical:
            result.append("")
            continue
        while _display_width(logical) > width:
            chunk = _take_cells(logical, width)
            if not chunk:
                break
            result.append(chunk)
            logical = logical[len(chunk) :]
        result.append(logical)
    return result or [""]


def _normalize_block(line: str) -> str:
    heading = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
    if heading:
        return "▌ " + heading.group(1).strip()
    bullet = re.match(r"^(\s*)([-*+] |\d+[.)] )(.*)$", line)
    if bullet:
        marker = "• " if not bullet.group(2)[0].isdigit() else "· "
        return bullet.group(1) + marker + bullet.group(3)
    quote = re.match(r"^\s*>\s?(.*)$", line)
    if quote:
        return "│ " + quote.group(1)
    return line


def _normalize_inline(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        groups = match.groups()
        # Delimiters occupy the first group of each alternative; return the
        # corresponding captured content instead of leaking `` ` ``/``*``.
        for index in (1, 3, 4, 5):
            if groups[index] is not None:
                return groups[index]
        return ""

    return _INLINE.sub(replace, line)


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _display_width(text: str) -> int:
    return sum(_char_width(char) for char in text)


def _take_cells(text: str, width: int) -> str:
    result: list[str] = []
    used = 0
    for char in text:
        size = _char_width(char)
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result)


__all__ = ["render_markdown"]
