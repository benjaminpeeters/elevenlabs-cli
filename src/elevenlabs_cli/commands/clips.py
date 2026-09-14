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
    parser.add_argument("--ext", default=".m4a", help="clip container: .m4a (default, AAC for the apps), .wav, .flac, .mp3, .opus")
    parser.add_argument("--out-dir", required=True, help="folder for the clips and manifest.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-truncated", dest="allow_truncated", action="store_true", help="keep renders whose tail the API cut short")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    text = read_text_input(args.text, args.file)
    lines = [line.strip() for line in text.splitlines()]
    utterances = [line for line in lines if line and not line.startswith("#")]
    if not utterances:
        raise CliError("no utterances: every line is empty or a comment")
    models = [ctx.model_for_text(args.model, u) for u in utterances]
    for utterance, (model, _) in zip(utterances, models):
        if len(utterance) > model_info(model).max_chars:
            raise CliError(f"an utterance exceeds the {model_info(model).max_chars}-character limit of {model}")
    chars = sum(billable_chars(u) for u in utterances)
    credits = sum(estimate_credits(u, m) for u, (m, _) in zip(utterances, models))
    switched = sum(1 for _, sw in models if sw)
    summary = f"utterances: {len(utterances)} ({switched} short, rendered with {ctx.config.get('short_line_model')})" if switched else f"utterances: {len(utterances)}"
    if args.dry_run:
        print(f"model: {ctx.model(args.model)}\n{summary}\ncharacters: {chars}\ncredits: {credits:g}")
        return
    model = ctx.model(args.model)
    audio.delivery_args(args.ext, 1)  # validates the extension early
    out_dir = ctx.resolve_out(args.out_dir, "--out-dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    confirm_spend(ctx, Spend(chars, credits, f"{len(utterances)} request(s) with {model}"))
    voice_id = resolve_voice(ctx, args.voice, args.language)
    fmt = ctx.api_format(args.format, "wav")
    settings = settings_from(args)
    workdir = ctx.workdir("clips")
    threshold = ctx.config.get("trim_threshold_db")
    margin = ctx.config.get("trim_margin_ms")
    entries: list[dict[str, Any]] = []
    for index, utterance in enumerate(utterances, start=1):
        line_model, _ = models[index - 1]
        result = api.text_to_speech(ctx.client, voice_id, utterance, line_model, fmt, args.language if line_model != "eleven_multilingual_v2" else None, settings, args.seed, None, None, None)
        raw = workdir / f"{index:03d}_raw.wav"
        audio.write_api_audio(result.audio, fmt, raw, workdir / "decode", 1)
        tail = audio.check_not_truncated(raw, ctx.config.get("truncation_db"), f"line {index} ({utterance[:40]!r})", args.allow_truncated)
        trimmed_wav = workdir / f"{index:03d}_trim.wav"
        trimmed = audio.trim(raw, trimmed_wav, threshold, margin, ctx.sample_rate, ctx.config.channels, ctx.config.get("default_lufs"))
        target = out_dir / f"{index:03d}{args.ext}"
        audio.encode(trimmed_wav, target, ctx.sample_rate, ctx.config.channels)
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
                "model": line_model,
                "seed": args.seed,
                "request_id": result.request_id,
                "tail_peak_db": round(tail.tail_peak_db, 1),
            }
        )
        say(f"clip {index}/{len(utterances)}: {target.name} ({entries[-1]['duration']} s)")
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps({"threshold_db": threshold, "margin_ms": margin, "sample_rate": ctx.sample_rate, "channels": ctx.config.channels, "clips": entries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_saved(manifest)
