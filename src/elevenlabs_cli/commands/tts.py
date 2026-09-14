"""``tts``: text to speech, chunked under the model limit, one file out."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, read_text_input, report_saved, resolve_voice, say
from ..cost import billable_chars, chunk_text, estimate_credits, model_info


def add_voice_settings(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stability", type=float, help="0..1 (v3 accepts 0.0, 0.5, 1.0: creative, natural, robust)")
    parser.add_argument("--similarity", type=float, help="0..1")
    parser.add_argument("--style", type=float, help="0..1 (v2 only)")
    parser.add_argument("--speed", type=float, help="0.7..1.2")
    parser.add_argument("--speaker-boost", dest="speaker_boost", action="store_true")


def settings_from(args: Any) -> api.Settings:
    return api.Settings(
        stability=args.stability,
        similarity=args.similarity,
        style=args.style,
        speed=args.speed,
        speaker_boost=True if args.speaker_boost else None,
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("tts", help="text to speech into one file")
    parser.add_argument("--text")
    parser.add_argument("--file", help="UTF-8 text file")
    parser.add_argument("--voice", required=True, help="alias from the config, exact voice name, or voice id")
    parser.add_argument("--model", help="default: config default_model")
    parser.add_argument("--language", help="ISO 639-1 code; also selects the alias language")
    parser.add_argument("--format", help="API output format (default: config default_format)")
    add_voice_settings(parser)
    parser.add_argument("--seed", type=int, help="fixed seed for reproducible delivery (0..4294967295)")
    parser.add_argument("--out", help="output file; extension picks the container (.mp3 raw, .wav/.m4a/.opus/.flac via ffmpeg)")
    parser.add_argument("--dry-run", action="store_true", help="print characters, chunks and credits, generate nothing")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    text = read_text_input(args.text, args.file)
    model = ctx.model(args.model)
    info = model_info(model)
    chunks = chunk_text(text, model)
    chars = billable_chars(text)
    credits = estimate_credits(text, model)
    if args.dry_run:
        print(f"model: {model}\ncharacters: {chars}\nchunks: {len(chunks)} (limit {info.max_chars})\ncredits: {credits:g}")
        return
    out = ctx.resolve_out(args.out)
    confirm_spend(ctx, Spend(chars, credits, f"{len(chunks)} request(s) with {model}"))
    voice_id = resolve_voice(ctx, args.voice, args.language)
    fmt = ctx.output_format(args.format)
    settings = settings_from(args)
    workdir = ctx.workdir("tts")
    parts: list[Path] = []
    request_ids: list[str] = []
    for index, chunk in enumerate(chunks):
        previous_text = chunks[index - 1] if index > 0 and info.stitching else None
        next_text = chunks[index + 1] if index + 1 < len(chunks) and info.stitching else None
        result = api.text_to_speech(
            ctx.client, voice_id, chunk, model, fmt, args.language, settings, args.seed,
            request_ids[-3:] if info.stitching else None, previous_text, next_text,
        )
        if result.request_id:
            request_ids.append(result.request_id)
        if len(chunks) == 1:
            audio.decode_api_audio(result.audio, fmt, out, workdir)
        else:
            part = workdir / f"{index:03d}.wav"
            audio.decode_api_audio(result.audio, fmt, part, workdir / "decode")
            parts.append(part)
            say(f"chunk {index + 1}/{len(chunks)} done")
    if len(chunks) > 1:
        audio.join([audio.JoinItem(file=str(p)) for p in parts], out, workdir / "join", ctx.config.get("trim_threshold_db"), 0, do_trim=False, lufs=None)
    report_saved(out)
