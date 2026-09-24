"""Timed scripts: text lines and ``[pause N]`` lines, rendered as one take and re-timed (``piece``).

A script line is one utterance the take is cut at; a ``[pause 2.5]`` line is
the exact silence the assembled piece carries at that point. The whole script
goes to the API as one request, so the voice keeps one tone and one room
throughout; the pauses only need to exist in the take (the model must leave a
silence there), their length is set locally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cost import ModelInfo
from .errors import CliError

SACRIFICIAL_TAIL = "Alright, then."  # v3 cuts the tail of a request's last utterance; this line absorbs the cut and is discarded
MAX_BREAK = 3.0  # seconds; the longest SSML break v2-class models accept
PAUSE_LINE = re.compile(r"^\[pause\s+(.+?)\s*\]$", re.IGNORECASE)


@dataclass(frozen=True)
class Line:
    number: int  # line number in the script file
    text: str


@dataclass(frozen=True)
class Pause:
    number: int
    seconds: float


def parse_timed_script(text: str) -> list[Line | Pause]:
    """Text lines and ``[pause <seconds>]`` lines; blank lines and ``#`` comments skipped; consecutive pauses add up."""
    items: list[Line | Pause] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PAUSE_LINE.match(line)
        if match:
            try:
                seconds = float(match.group(1))
            except ValueError as exc:
                raise CliError(f"line {number}: expected [pause <seconds>], got '{line}'") from exc
            if seconds <= 0:
                raise CliError(f"line {number}: a pause must be positive, got {seconds:g}")
            if items and isinstance(items[-1], Pause):
                previous = items[-1]
                items[-1] = Pause(previous.number, round(previous.seconds + seconds, 6))
            else:
                items.append(Pause(number, seconds))
            continue
        if line.lower().startswith("[pause"):
            raise CliError(f"line {number}: expected [pause <seconds>], got '{line}'")
        items.append(Line(number, line))
    if not any(isinstance(item, Line) for item in items):
        raise CliError("no text line in the script: every line is empty, a comment or a pause")
    return items


def lines_of(items: list[Line | Pause]) -> list[Line]:
    return [item for item in items if isinstance(item, Line)]


def pauses_of(items: list[Line | Pause]) -> list[Pause]:
    return [item for item in items if isinstance(item, Pause)]


def request_text(items: list[Line | Pause], info: ModelInfo) -> str:
    """The one request for the whole script.

    v2-class models (SSML breaks, no audio tags) get a ``<break>`` wherever the
    script pauses, capped at 3 s, so the model leaves a silence to cut at.
    v3-class models get one paragraph per line and a sacrificial closing
    sentence that absorbs the tail cut; leading and trailing pauses never
    reach the API, they are added at assembly.
    """
    lines = lines_of(items)
    if info.audio_tags:
        return "\n\n".join(line.text for line in lines) + " " + SACRIFICIAL_TAIL
    parts: list[str] = []
    pending: float | None = None
    for item in items:
        if isinstance(item, Pause):
            pending = item.seconds
            continue
        if parts:
            parts.append(f' <break time="{min(pending, MAX_BREAK):.1f}s"/> ' if pending is not None else " ")
        parts.append(item.text)
        pending = None
    return "".join(parts)


def locate_spans(chars: str, lines: list[str]) -> list[tuple[int, int]]:
    """Where each line's text sits in the alignment's character string, as (first, last) indexes.

    Searched in order from the previous line's end, so it works whether the
    alignment carries the break tags and separators or not. A line the API
    normalised away is an error: no guessing which characters are which.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for number, text in enumerate(lines, start=1):
        at = chars.find(text, cursor)
        if at < 0:
            raise CliError(f"line {number} ('{text[:40]}') not found in the alignment the API returned; the text was normalised on the way")
        spans.append((at, at + len(text) - 1))
        cursor = at + len(text)
    return spans
