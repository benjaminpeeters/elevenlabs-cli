"""``dialogue``: multi-speaker audio with natural turn-taking.

Three mechanisms, chosen with ``--mode`` (their merits are compared by
experiment and recorded in the lab):

- ``per-turn``: every line is its own text-to-speech request with the
  speaker's voice, model, settings and seed, then the lines are placed on a
  timeline by the turn policy (``turns.py``): varied gaps, quick replies,
  marked overlaps.
- ``segments``: the single-request dialogue endpoint (the model hears the
  whole exchange), whose stream is cut into per-line clips at the voice
  segments it reports, then re-timed by the same policy.
- ``single``: the dialogue endpoint's own output, untouched (chunked under
  2000 characters).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .. import audio, turns
from .. import client as api
from ..common import Context, Spend, confirm_spend, read_text_input, report_saved, resolve_voice, say, table
from ..cost import billable_chars, estimate_credits, model_info
from ..errors import CliError
from .join import sidecar_path
from .verify import check_and_report

DIALOGUE_REQUEST_CHARS = 2000  # the endpoint's recommended maximum per request
SACRIFICIAL_LINE = "Alright, then."  # appended to every dialogue request; its clip is thrown away
MODES = ("per-turn", "segments", "single")


@dataclass(frozen=True)
class SpeakerProfile:
    name: str
    voice: str  # alias, name or id as typed
    voice_id: str = ""
    model: str | None = None
    settings: api.Settings = api.Settings()


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "dialogue",
        help="multi-speaker audio with natural turn-taking",
        description="Lines read 'Name: text'; a line may start with [gap 1.2], [quick] or [overlap 0.4]. "
        "Map every name with --speaker 'Name=voice[,model=...,stability=...,speed=...,similarity=...,style=...]'.",
    )
    parser.add_argument("--text")
    parser.add_argument("--file")
    parser.add_argument("--speaker", action="append", default=[], metavar="NAME=VOICE[,k=v...]", help="repeat per speaker")
    parser.add_argument("--mode", choices=MODES, default="per-turn", help="per-turn (default), segments, or single")
    parser.add_argument("--model", help="default model for speakers without model= (default: config default_model)")
    parser.add_argument("--language")
    parser.add_argument("--format")
    parser.add_argument("--stability", type=float, help="segments/single: the dialogue endpoint's stability")
    parser.add_argument("--seed", type=int, help="seed for the turn plan and the speakers (default: derived from the script)")
    parser.add_argument("--lufs", type=float, help="per-line loudness target (default: config default_lufs)")
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true", help="print the turn plan and the credits, spend nothing")
    parser.add_argument("--allow-truncated", dest="allow_truncated", action="store_true", help="keep renders whose tail the API cut short")
    parser.set_defaults(func=run)


def parse_lines(text: str) -> list[tuple[str, str]]:
    """Kept for callers that only need (speaker, text) pairs."""
    return [(line.speaker, line.text) for line in turns.parse_script(text)]


def parse_speakers(entries: list[str]) -> dict[str, SpeakerProfile]:
    speakers: dict[str, SpeakerProfile] = {}
    for entry in entries:
        name, sep, rest = entry.partition("=")
        if not sep or not name or not rest:
            raise CliError(f"--speaker expects NAME=VOICE[,k=v...], got '{entry}'")
        parts = [p.strip() for p in rest.split(",")]
        voice = parts[0]
        model = None
        values: dict[str, float] = {}
        for part in parts[1:]:
            key, eq, value = part.partition("=")
            if not eq:
                raise CliError(f"--speaker {name}: expected k=v, got '{part}'")
            if key == "model":
                model_info(value)  # validates
                model = value
            elif key in ("stability", "speed", "similarity", "style"):
                try:
                    values[key] = float(value)
                except ValueError as exc:
                    raise CliError(f"--speaker {name}: {key} must be a number, got '{value}'") from exc
            else:
                raise CliError(f"--speaker {name}: unknown setting '{key}' (model, stability, speed, similarity, style)")
        speakers[name] = SpeakerProfile(name, voice, "", model, api.Settings(values.get("stability"), values.get("similarity"), values.get("style"), values.get("speed"), None))
    return speakers


def chunk_lines(lines: list[turns.Line], limit: int = DIALOGUE_REQUEST_CHARS) -> list[list[turns.Line]]:
    chunks: list[list[turns.Line]] = [[]]
    size = len(SACRIFICIAL_LINE)
    for line in lines:
        if len(line.text) > limit:
            raise CliError(f"line {line.number} has {len(line.text)} characters, above the {limit} the dialogue endpoint accepts")
        if size + len(line.text) > limit and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += len(line.text)
    return chunks


def items_from_plan(clips: list[Path], lines: list[turns.Line], plan: list[turns.Turn]) -> list[audio.JoinItem]:
    """Timeline items: every clip, with the planned gap or overlap before it."""
    by_index = {t.index: t for t in plan}
    items: list[audio.JoinItem] = []
    for index, (clip, line) in enumerate(zip(clips, lines)):
        turn = by_index.get(index)
        if turn is not None:
            items.append(audio.JoinItem(silence=turn.seconds) if turn.kind == "gap" and turn.seconds > 0 else audio.JoinItem(overlap=turn.seconds if turn.kind == "overlap" else 0.0))
        items.append(audio.JoinItem(file=str(clip), label=f"{line.number} {line.speaker}"))
    return items


def render_per_turn(ctx: Context, lines: list[turns.Line], profiles: dict[str, SpeakerProfile], fmt: str, language: str | None, seed: int, workdir: Path, allow_truncated: bool, default_model: str | None) -> list[Path]:
    clips: list[Path] = []
    for index, line in enumerate(lines):
        profile = profiles[line.speaker]
        model, _ = ctx.model_for_text(profile.model or default_model, line.text)
        # continuity context is a v2-class feature: v3 rejects previous_text/next_text (HTTP 400, "not yet supported")
        continuity = model_info(model).stitching
        raw = workdir / f"{index:03d}_{line.speaker}.wav"
        line_seed = turns.speaker_seed(seed, line.speaker)
        if continuity:
            previous = lines[index - 1].text if index > 0 else None
            following = lines[index + 1].text if index + 1 < len(lines) else None
            result = api.text_to_speech(ctx.client, profile.voice_id, line.text, model, fmt, None, profile.settings, line_seed, None, previous, following)
            audio.write_api_audio(result.audio, fmt, raw, workdir / "decode", 1)
        else:
            render_with_sacrificial_tail(ctx, profile.voice_id, line.text, model, fmt, language, profile.settings, line_seed, raw, workdir / "decode")
        audio.check_not_truncated(raw, ctx.config.get("truncation_db"), f"line {line.number} ({line.speaker})", allow_truncated)
        clips.append(raw)
        say(f"line {line.number}/{lines[-1].number}: {line.speaker}, {model}")
    return clips


def render_with_sacrificial_tail(ctx: Context, voice_id: str, text: str, model: str, fmt: str, language: str | None, settings: api.Settings, seed: int | None, out: Path, workdir: Path) -> None:
    """v3 cuts the tail of a request's last utterance, so render ``text`` followed by a throwaway
    sentence and cut the real line out at the silence between them (character alignment + silence search)."""
    full = text + " " + SACRIFICIAL_LINE
    result = api.text_to_speech_timed(ctx.client, voice_id, full, model, fmt, language, settings, seed, None, None)
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / (out.stem + "_full.wav")
    audio.write_api_audio(result.audio, fmt, raw, workdir, 1)
    if result.alignment is None:
        raise CliError(f"the API returned no alignment for '{text[:40]}'; cannot cut the sacrificial tail")
    chars = "".join(result.alignment.characters)
    if chars != full:
        raise CliError(f"alignment characters do not match the text for '{text[:40]}' ({len(chars)} vs {len(full)})")
    audio.cut_line_by_alignment(raw, out, result.alignment.starts, result.alignment.ends, 0, len(text) - 1, ctx.config.get("trim_threshold_db"), ctx.sample_rate, ctx.config.channels)


def render_segments(ctx: Context, lines: list[turns.Line], profiles: dict[str, SpeakerProfile], model: str, fmt: str, language: str | None, stability: float | None, seed: int, workdir: Path, allow_truncated: bool) -> list[Path]:
    """One dialogue request per chunk, cut into per-line clips at the reported voice segments."""
    clips: list[Path] = []
    offset = 0
    for number, chunk in enumerate(chunk_lines(lines)):
        # v3 cuts the tail of the last utterance of a request: a short sacrificial line by the last
        # speaker absorbs that cut and is discarded after the segments are separated
        inputs = [(profiles[l.speaker].voice_id, l.text) for l in chunk] + [(profiles[chunk[-1].speaker].voice_id, SACRIFICIAL_LINE)]
        result = api.text_to_dialogue_timed(ctx.client, inputs, model, fmt, language, stability, seed)
        raw = workdir / f"chunk{number:02d}.wav"
        audio.write_api_audio(result.audio, fmt, raw, workdir / "decode", 1)
        duration = audio.probe(raw).duration
        segments = sorted(result.segments)
        (workdir / f"chunk{number:02d}_segments.json").write_text(json.dumps(segments), encoding="utf-8")
        by_line: dict[int, list[tuple[float, float]]] = {}
        for line_index, start, end in segments:
            by_line.setdefault(line_index, []).append((start, end))
        for local, line in enumerate(chunk):
            spans = by_line.get(local)
            if not spans:
                raise CliError(f"the dialogue endpoint returned no voice segment for line {line.number}; use --mode per-turn")
            start = spans[0][0]
            end = spans[-1][1]
            # the next line's own segment start (segments of consecutive lines touch, so a gap-based search would skip it)
            later = [s for (li, s, _) in segments if li > local]
            next_start = min(later) if later else duration
            clip = workdir / f"{offset + local:03d}_{line.speaker}.wav"
            cut_end = audio.boundary_after(raw, end, next_start, ctx.config.get("trim_threshold_db"))
            audio.cut_span(raw, clip, max(0.0, start - 0.05), cut_end, ctx.sample_rate, ctx.config.channels)
            audio.check_not_truncated(clip, ctx.config.get("truncation_db"), f"line {line.number} ({line.speaker})", allow_truncated)
            clips.append(clip)
        offset += len(chunk)
    return clips


def render_dialogue(ctx: Context, script: str, profiles: dict[str, SpeakerProfile], mode: str, out: Path, *, fmt: str, language: str | None, model: str | None, stability: float | None, seed: int | None, lufs: float | None, allow_truncated: bool) -> audio.Timing:
    """Render a script in one of the three modes and return the timing (used by dialogue and by auditions)."""
    lines = turns.parse_script(script)
    missing = sorted({l.speaker for l in lines} - set(profiles))
    if missing:
        raise CliError(f"no --speaker mapping for: {', '.join(missing)}")
    resolved = {name: replace(p, voice_id=resolve_voice(ctx, p.voice, language)) for name, p in profiles.items()}
    base_seed = seed if seed is not None else turns.script_seed(script)
    workdir = ctx.workdir("dialogue")
    threshold = ctx.config.get("trim_threshold_db")
    margin = ctx.config.get("trim_margin_ms")
    if mode == "single":
        endpoint_model = ctx.model(model)
        if not model_info(endpoint_model).audio_tags:
            raise CliError(f"the dialogue endpoint needs a v3-class model, not {endpoint_model}")
        parts: list[Path] = []
        for number, chunk in enumerate(chunk_lines(lines)):
            data = api.text_to_dialogue(ctx.client, [(resolved[l.speaker].voice_id, l.text) for l in chunk], endpoint_model, fmt, language, stability, seed)
            raw = workdir / f"chunk{number:02d}.wav"
            audio.write_api_audio(data, fmt, raw, workdir / "decode", 1)
            audio.check_not_truncated(raw, ctx.config.get("truncation_db"), f"chunk {number + 1}", allow_truncated)
            parts.append(raw)
        items = [audio.JoinItem(file=str(p)) for p in parts]
        timing = audio.join(items, out, workdir / "join", threshold, 0, False, lufs, ctx.sample_rate, None)
    else:
        plan = turns.plan_turns(lines, base_seed, ctx.config.get("turn_gap_min"), ctx.config.get("turn_gap_max"))
        if mode == "per-turn":
            clips = render_per_turn(ctx, lines, resolved, fmt, language, base_seed, workdir, allow_truncated, model)
        else:
            endpoint_model = ctx.model(model)
            if not model_info(endpoint_model).audio_tags:
                raise CliError(f"the dialogue endpoint needs a v3-class model, not {endpoint_model}")
            clips = render_segments(ctx, lines, resolved, endpoint_model, fmt, language, stability, base_seed, workdir, allow_truncated)
        items = items_from_plan(clips, lines, plan)
        timing = audio.join(items, out, workdir / "join", threshold, margin, True, lufs, ctx.sample_rate, None)
    sidecar = sidecar_path(out)
    data = timing.to_json().rstrip("\n}") + f',\n  "mode": "{mode}",\n  "seed": {base_seed}\n}}\n'
    sidecar.write_text(data, encoding="utf-8")
    return timing


def run(args: Any) -> None:
    ctx = Context(args)
    script = read_text_input(args.text, args.file)
    lines = turns.parse_script(script)
    profiles = parse_speakers(args.speaker)
    missing = sorted({l.speaker for l in lines} - set(profiles))
    if missing:
        raise CliError(f"no --speaker mapping for: {', '.join(missing)}")
    if args.mode == "per-turn":
        models = [ctx.model_for_text(profiles[l.speaker].model or args.model, l.text)[0] for l in lines]
    else:
        endpoint = ctx.model(args.model)
        if not model_info(endpoint).audio_tags:
            raise CliError(f"the dialogue endpoint needs a v3-class model, not {endpoint}")
        models = [endpoint] * len(lines)
    chars = sum(billable_chars(l.text) for l in lines)
    credits = sum(estimate_credits(l.text, m) for l, m in zip(lines, models))
    if args.dry_run:
        seed = args.seed if args.seed is not None else turns.script_seed(script)
        plan = turns.plan_turns(lines, seed, ctx.config.get("turn_gap_min"), ctx.config.get("turn_gap_max"))
        rows = [(n, sp, ch, turn, reason, m) for (n, sp, ch, turn, reason), m in zip(turns.describe(lines, plan), models)]
        print(table(rows, ("line", "speaker", "chars", "turn", "reason", "model")))
        print(f"mode: {args.mode}\nseed: {seed}\ncharacters: {chars}\ncredits: {credits:g}")
        return
    out = ctx.resolve_out(args.out)
    confirm_spend(ctx, Spend(chars, credits, f"{len(lines)} lines, {len(profiles)} speakers, mode {args.mode}"))
    timing = render_dialogue(
        ctx, script, profiles, args.mode, out,
        fmt=ctx.api_format(args.format, "wav"), language=args.language, model=args.model, stability=args.stability,
        seed=args.seed, lufs=args.lufs if args.lufs is not None else ctx.config.get("default_lufs"), allow_truncated=args.allow_truncated,
    )
    report_saved(out)
    say(f"timing: {sidecar_path(out)} ({len(timing.clips)} lines, {len(timing.gaps)} gaps, {len(timing.overlaps)} overlaps, {timing.duration:.1f} s)")
    verifiable = [g for g in timing.gaps if g.verifiable]
    if verifiable:
        check_and_report(ctx, out, [g.expected for g in verifiable], positions=[(g.start, g.end) for g in verifiable])
