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
_ANSI_STYLE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_INLINE_STYLE = re.compile(r"(`+)(.*?)\1|(\*\*|__)(.+?)\3|(\*|_)(.+?)\5")
_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/][^\s`]+|(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z0-9_-]+)")


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


def render_markdown_styled(text: str, width: int) -> list[str]:
    """Render assistant Markdown with Pi-like semantic ANSI accents.

    The regular ``render_markdown`` function intentionally stays plain for
    callers that need stable text.  This presentation variant adds styles
    after parsing blocks and wraps by visible terminal cells, so ANSI bytes do
    not affect layout width.
    """
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
            styled = "\x1b[38;5;114m" + logical + "\x1b[0m"
        else:
            heading = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
            bullet = re.match(r"^(\s*)([-*+] |\d+[.)] )(.*)$", line)
            quote = re.match(r"^\s*>\s?(.*)$", line)
            if heading:
                logical = "▌ " + heading.group(1).strip()
                styled = "\x1b[33;1m" + logical + "\x1b[0m"
            elif bullet:
                marker = "• " if not bullet.group(2)[0].isdigit() else "· "
                logical = bullet.group(1) + marker + bullet.group(3)
                styled = (
                    bullet.group(1)
                    + "\x1b[38;5;116m"
                    + marker
                    + "\x1b[0m"
                    + _style_inline(bullet.group(3))
                )
            elif quote:
                logical = "│ " + quote.group(1)
                styled = "\x1b[90m│ \x1b[0m" + _style_inline(quote.group(1))
            else:
                logical = line
                styled = _style_inline(logical)
        result.extend(_wrap_styled(styled, width)) if logical else result.append("")
    return result or [""]


def _style_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_STYLE.finditer(text):
        parts.append(_style_paths(text[cursor : match.start()]))
        if match.group(2) is not None:
            parts.append("\x1b[36m" + match.group(2) + "\x1b[0m")
        elif match.group(4) is not None:
            parts.append("\x1b[1m" + match.group(4) + "\x1b[0m")
        else:
            parts.append("\x1b[3m" + (match.group(6) or "") + "\x1b[0m")
        cursor = match.end()
    parts.append(_style_paths(text[cursor:]))
    return "".join(parts)


def _style_paths(text: str) -> str:
    return _PATH.sub(lambda match: "\x1b[38;5;116m" + match.group(0) + "\x1b[0m", text)


def _wrap_styled(text: str, width: int) -> list[str]:
    result: list[str] = []
    rest = text
    while rest:
        used = 0
        index = 0
        while index < len(rest):
            ansi = _ANSI_STYLE.match(rest, index)
            if ansi:
                index = ansi.end()
                continue
            size = _char_width(rest[index])
            if used + size > width:
                break
            used += size
            index += 1
        if index == 0:
            break
        chunk = rest[:index]
        result.append(chunk)
        rest = rest[index:]
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


__all__ = ["render_markdown", "render_markdown_styled"]
