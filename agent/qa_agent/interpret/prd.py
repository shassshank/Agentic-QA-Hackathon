"""Deterministic PRD splitter: markdown → normative units (list items and prose paragraphs)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import PrdBullet

# Sections that frame the test effort rather than describe target behavior. They are passed to the
# interpreter as context but no requirement must be derived from them.
CONTEXT_SECTIONS = {"out of scope", "requirement coverage expectations"}

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$")


@dataclass
class ParsedPrd:
    title: str = ""
    bullets: list[PrdBullet] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)


def clean_inline(text: str) -> str:
    """Strip markdown emphasis and code markers so quotes can be matched verbatim."""
    text = re.sub(r"(\*\*|__|`)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_prd(markdown: str) -> ParsedPrd:
    parsed = ParsedPrd()
    section = ""
    current: list[str] = []
    current_line = 0
    context_lines: dict[str, list[str]] = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = clean_inline(" ".join(current))
        if section.lower() in CONTEXT_SECTIONS:
            context_lines.setdefault(section, []).append(text)
        elif text:
            parsed.bullets.append(
                PrdBullet(id=f"B{len(parsed.bullets) + 1:02d}", section=section, line=current_line, text=text)
            )
        current = []

    for number, raw in enumerate(markdown.splitlines(), start=1):
        line = raw.rstrip()
        heading = _HEADING.match(line)
        if heading:
            flush()
            level, title = len(heading.group(1)), clean_inline(heading.group(2))
            if level == 1 and not parsed.title:
                parsed.title = title
            else:
                section = title
            continue
        if not line.strip():
            flush()
            continue
        item = _LIST_ITEM.match(line)
        if item:
            flush()
            current, current_line = [item.group(1)], number
        elif current and raw.startswith((" ", "\t")):
            current.append(line.strip())  # continuation of a wrapped list item
        else:
            if not current:
                current_line = number
            current.append(line.strip())

    flush()
    parsed.context = {name: "\n".join(lines) for name, lines in context_lines.items()}
    return parsed
