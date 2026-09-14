"""``dialogue``: multi-speaker generation (v3) from 'Name: line' text."""

from __future__ import annotations

import argparse
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, read_text_input, report_saved, resolve_voice
from ..cost import billable_chars, estimate_credits, model_info
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "dialogue",
        help="multi-speaker audio from 'Name: line' text (v3)",
        description="Each line reads 'Name: text'. Map every name to a voice with --speaker Name=voice (alias, exact name or id).",
    )
    parser.add_argument("--text")
    parser.add_argument("--file")
    parser.add_argument("--speaker", action="append", default=[], metavar="NAME=VOICE", help="repeat per speaker")
    parser.add_argument("--model", help="must support audio tags (default: config default_model)")
    parser.add_argument("--language")
    parser.add_argument("--format")
    parser.add_argument("--stability", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(func=run)


def parse_lines(text: str) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, spoken = line.partition(":")
        if not sep or not name.strip() or not spoken.strip():
            raise CliError(f"line {number}: expected 'Name: text', got '{line}'")
        lines.append((name.strip(), spoken.strip()))
    if not lines:
        raise CliError("no dialogue lines found")
    return lines


def parse_speakers(entries: list[str]) -> dict[str, str]:
    speakers: dict[str, str] = {}
    for entry in entries:
        name, sep, voice = entry.partition("=")
        if not sep or not name or not voice:
            raise CliError(f"--speaker expects NAME=VOICE, got '{entry}'")
        speakers[name] = voice
    return speakers


def run(args: Any) -> None:
    ctx = Context(args)
    lines = parse_lines(read_text_input(args.text, args.file))
    speakers = parse_speakers(args.speaker)
    missing = sorted({name for name, _ in lines} - set(speakers))
    if missing:
        raise CliError(f"no --speaker mapping for: {', '.join(missing)}")
    model = ctx.model(args.model)
    if not model_info(model).audio_tags:
        raise CliError(f"dialogue needs a v3-class model, not {model}")
    chars = sum(billable_chars(t) for _, t in lines)
    credits = sum(estimate_credits(t, model) for _, t in lines)
    if args.dry_run:
        print(f"model: {model}\nlines: {len(lines)}\ncharacters: {chars}\ncredits: {credits:g}")
        return
    out = ctx.resolve_out(args.out)
    confirm_spend(ctx, Spend(chars, credits, f"{len(lines)} lines, {len(speakers)} speakers, {model}"))
    voice_ids = {name: resolve_voice(ctx, voice, args.language) for name, voice in speakers.items()}
    fmt = ctx.output_format(args.format)
    data = api.text_to_dialogue(ctx.client, [(voice_ids[name], text) for name, text in lines], model, fmt, args.language, args.stability, args.seed)
    audio.decode_api_audio(data, fmt, out, ctx.workdir("dialogue"))
    report_saved(out)
