"""``clips``: one trimmed file per line of text, plus a manifest, for app-timed playback."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, read_text_input, report_saved, resolve_voice, say
from ..cost import billable_chars, estimate_credits, model_info
from ..errors import CliError
from .tts import add_voice_settings, settings_from


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "clips",
        help="render and trim one utterance per line into separate files with a manifest",
        description="Each non-empty line (comments start with #) becomes <index>.<ext> trimmed to its voiced content; "
        "manifest.json lists id, text, file, duration, sha256 and the kept margins. The player inserts the silences.",
    )
    parser.add_argument("--text", help="a single utterance")
    parser.add_argument("--file", help="UTF-8 text file, one utterance per line")
    parser.add_argument("--voice", required=True)
    parser.add_argument("--model")
    parser.add_argument("--language")
    parser.add_argument("--format")
    add_voice_settings(parser)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--ext", default=".wav", help="clip container: .wav (default), .m4a, .mp3, .opus, .flac")
    parser.add_argument("--out-dir", required=True, help="folder for the clips and manifest.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    text = read_text_input(args.text, args.file)
    lines = [line.strip() for line in text.splitlines()]
    utterances = [line for line in lines if line and not line.startswith("#")]
    if not utterances:
        raise CliError("no utterances: every line is empty or a comment")
    model = ctx.model(args.model)
    info = model_info(model)
    too_long = [u for u in utterances if len(u) > info.max_chars]
    if too_long:
        raise CliError(f"{len(too_long)} utterance(s) exceed the {info.max_chars}-character limit of {model}")
    chars = sum(billable_chars(u) for u in utterances)
    credits = sum(estimate_credits(u, model) for u in utterances)
    if args.dry_run:
        print(f"model: {model}\nutterances: {len(utterances)}\ncharacters: {chars}\ncredits: {credits:g}")
        return
    if args.ext not in audio.ENCODERS:
        raise CliError(f"unsupported --ext '{args.ext}' (supported: {', '.join(audio.ENCODERS)})")
    out_dir = ctx.resolve_out(args.out_dir, "--out-dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    confirm_spend(ctx, Spend(chars, credits, f"{len(utterances)} request(s) with {model}"))
    voice_id = resolve_voice(ctx, args.voice, args.language)
    fmt = ctx.output_format(args.format)
    settings = settings_from(args)
    workdir = ctx.workdir("clips")
    threshold = ctx.config.get("trim_threshold_db")
    margin = ctx.config.get("trim_margin_ms")
    entries: list[dict[str, Any]] = []
    for index, utterance in enumerate(utterances, start=1):
        result = api.text_to_speech(ctx.client, voice_id, utterance, model, fmt, args.language, settings, args.seed, None, None, None)
        raw = workdir / f"{index:03d}_raw.wav"
        audio.decode_api_audio(result.audio, fmt, raw, workdir / "decode")
        trimmed_wav = workdir / f"{index:03d}_trim.wav"
        trimmed = audio.trim(raw, trimmed_wav, threshold, margin)
        target = out_dir / f"{index:03d}{args.ext}"
        audio.encode(trimmed_wav, target, None)
        entries.append(
            {
                "id": f"{index:03d}",
                "text": utterance,
                "file": target.name,
                "duration": round(audio.probe(target).duration, 3),
                "kept_leading": round(trimmed.kept_leading, 3),
                "kept_trailing": round(trimmed.kept_trailing, 3),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "voice_id": voice_id,
                "model": model,
                "seed": args.seed,
                "request_id": result.request_id,
            }
        )
        say(f"clip {index}/{len(utterances)}: {target.name} ({entries[-1]['duration']} s)")
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps({"threshold_db": threshold, "margin_ms": margin, "clips": entries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_saved(manifest)
