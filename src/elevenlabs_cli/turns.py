"""Turn-taking for dialogues: script markers and a seeded, rule-based gap policy.

Pure Python, no ffmpeg, no API. A script line reads ``Name: text`` and may
start its text with one marker: ``[gap 1.2]`` (a deliberate pause before
this line), ``[quick]`` (this line comes right after the previous one),
``[overlap 0.4]`` (this line starts 0.4 s before the previous one ends).
Without a marker the gap is drawn from a seeded triangular distribution
inside ``gap_min..gap_max``, narrowed by what the two lines look like, so no
two turns feel the same and the same script and seed always give the same plan.
"""

from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass

from .errors import CliError

MARKER = re.compile(r"^\s*\[(gap|overlap)\s+([0-9.]+)\]\s*|^\s*\[(quick)\]\s*")
QUICK_GAP = 0.05
API_SEED_MAX = 4294967295


@dataclass(frozen=True)
class Line:
    number: int  # 1-based line number in the script
    speaker: str
    text: str  # marker stripped: what is rendered and billed
    gap: float | None = None
    overlap: float | None = None
    quick: bool = False


@dataclass(frozen=True)
class Turn:
    """How line ``index`` follows the line before it."""

    index: int
    kind: str  # "gap" | "overlap"
    seconds: float
    reason: str  # "marker" | "quick" | "question" | "short-reply" | "long-statement" | "same-speaker" | "default"


def parse_script(text: str) -> list[Line]:
    """``Name: [marker] text`` lines; blank lines and ``#`` comments are skipped."""
    lines: list[Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        speaker, sep, spoken = stripped.partition(":")
        if not sep or not speaker.strip() or not spoken.strip():
            raise CliError(f"line {number}: expected 'Name: text', got '{stripped}'")
        spoken = spoken.strip()
        gap = overlap = None
        quick = False
        match = MARKER.match(spoken)
        if match:
            if match.group(1) == "gap":
                gap = float(match.group(2))
            elif match.group(1) == "overlap":
                overlap = float(match.group(2))
            else:
                quick = True
            spoken = spoken[match.end():].strip()
            if not spoken:
                raise CliError(f"line {number}: nothing to say after the marker")
        elif spoken.startswith("[") and re.match(r"^\[(gap|overlap|quick)\b", spoken):
            raise CliError(f"line {number}: malformed marker in '{spoken[:30]}' (use [gap 1.2], [overlap 0.4] or [quick])")
        lines.append(Line(number, speaker.strip(), spoken, gap, overlap, quick))
    if not lines:
        raise CliError("no dialogue lines found")
    if lines[0].gap is not None or lines[0].overlap is not None or lines[0].quick:
        raise CliError(f"line {lines[0].number}: the first line cannot carry a turn marker")
    return lines


def script_seed(text: str) -> int:
    """A stable seed for a script: its CRC32 (never Python's salted hash)."""
    return zlib.crc32(text.encode("utf-8")) & API_SEED_MAX


def speaker_seed(base: int, speaker: str) -> int:
    """One seed per speaker, derived from the base, inside the API's range."""
    return (base + zlib.crc32(speaker.encode("utf-8"))) % (API_SEED_MAX + 1)


def plan_turns(lines: list[Line], seed: int, gap_min: float, gap_max: float) -> list[Turn]:
    """The gap or overlap before every line after the first.

    Rules for unmarked lines: after a question the reply comes fast
    (0.10..0.30 s); a reply of three words or fewer is quick (0.10..0.25);
    after a long statement or a trailing ellipsis the listener needs a moment
    (0.35..0.70); the same speaker continuing pauses longer (0.40..0.80);
    otherwise ``gap_min..gap_max``. Draws are triangular, clustered mid-range.
    """
    if gap_min < 0 or gap_max < gap_min:
        raise CliError(f"turn gaps must satisfy 0 <= min <= max, got {gap_min}..{gap_max}")
    rng = random.Random(seed)
    turns: list[Turn] = []
    for index in range(1, len(lines)):
        prev, cur = lines[index - 1], lines[index]
        if cur.overlap is not None:
            turns.append(Turn(index, "overlap", cur.overlap, "marker"))
            continue
        if cur.gap is not None:
            turns.append(Turn(index, "gap", cur.gap, "marker"))
            continue
        if cur.quick:
            turns.append(Turn(index, "gap", QUICK_GAP, "quick"))
            continue
        low, high, reason = gap_min, gap_max, "default"
        prev_words = len(prev.text.split())
        cur_words = len(cur.text.split())
        if prev.speaker == cur.speaker:
            low, high, reason = 0.40, 0.80, "same-speaker"
        elif prev.text.rstrip().endswith("?"):
            low, high, reason = 0.10, 0.30, "question"
        elif cur_words <= 3:
            low, high, reason = 0.10, 0.25, "short-reply"
        elif prev_words >= 25 or prev.text.rstrip().endswith(("...", "…")):
            low, high, reason = 0.35, 0.70, "long-statement"
        turns.append(Turn(index, "gap", round(rng.triangular(low, high), 3), reason))
    return turns


def describe(lines: list[Line], turns: list[Turn]) -> list[tuple[int, str, int, str, str]]:
    """Rows for a dry run: (line number, speaker, characters, turn text, reason)."""
    by_index = {t.index: t for t in turns}
    rows = []
    for index, line in enumerate(lines):
        turn = by_index.get(index)
        if turn is None:
            rows.append((line.number, line.speaker, len(line.text), "start", ""))
        else:
            rows.append((line.number, line.speaker, len(line.text), f"{turn.kind} {turn.seconds:.2f} s", turn.reason))
    return rows
