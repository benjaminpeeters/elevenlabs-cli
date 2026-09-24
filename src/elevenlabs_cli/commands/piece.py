"""``piece``: a timed script rendered as one take, cut at its pauses, re-timed exactly.

One request for the whole script (one tone, one room tone), the character
alignment locates every line, the silence after each line is the cut point,
and ``join`` reassembles the lines with the script's exact pauses. A pause
the model did not leave is an error, never a cut inside speech.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .. import audio, script
from .. import client as api
from ..common import Context, Spend, confirm_spend, read_text_input, report_saved, resolve_voice, say
from ..cost import billable_chars, estimate_credits, model_info
from ..errors import CliError
from .join import sidecar_path
from .tts import add_voice_settings, settings_from
from .verify import check_and_report


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "piece",
        help="one take of a timed script, cut at its pauses and re-timed exactly",
        description="A script is text lines and '[pause <seconds>]' lines (comments start with #). The whole script is one request, "
        "so the voice keeps one tone and one room throughout; the take is cut at the pauses the model left (character alignment plus "
        "silence search) and reassembled with the script's exact silences, leading and trailing ones included. "
        "v2-class models get an SSML break at every pause; v3-class models get one paragraph per line and a sacrificial closing sentence.",
    )
    parser.add_argument("--text")
    parser.add_argument("--file", help="UTF-8 timed script")
    parser.add_argument("--voice", required=True, help="alias from the config, exact voice name, or voice id")
    parser.add_argument("--model", help="default: config default_model (the short-line rule does not apply: one request)")
    parser.add_argument("--language")
    parser.add_argument("--format", help="API output format (default: wav_<sample_rate>)")
    add_voice_settings(parser)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--lufs", type=float, help="loudness target for the whole take, before cutting (default: config default_lufs)")
    parser.add_argument("--clips-dir", dest="clips_dir", help="also keep the cut lines as 001.wav... with a manifest.json")
    parser.add_argument("--out", help="output file (.wav, .m4a, .mp3, .opus, .flac); <out>.timing.json is written next to it")
    parser.add_argument("--dry-run", action="store_true", help="print lines, pauses, characters and credits, spend nothing")
    parser.add_argument("--allow-truncated", dest="allow_truncated", action="store_true", help="keep a line whose tail the API cut short")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    items = script.parse_timed_script(read_text_input(args.text, args.file))
    lines = script.lines_of(items)
    pauses = script.pauses_of(items)
    model = ctx.model(args.model)
    info = model_info(model)
    full = script.request_text(items, info)
    if len(full) > info.max_chars:
        raise CliError(f"the script is {len(full)} characters in one request, above the {info.max_chars} limit of {model}; split it into shorter pieces or use a model with a higher limit")
    chars = billable_chars(full)
    credits = estimate_credits(full, model)
    if args.dry_run:
        print(f"model: {model}\nlines: {len(lines)}\npauses: {len(pauses)} ({sum(p.seconds for p in pauses):g} s)\ncharacters: {chars}\nrequests: 1\ncredits: {credits:g}")
        return
    out = ctx.resolve_out(args.out)
    confirm_spend(ctx, Spend(chars, credits, f"1 request with {model}, {len(lines)} lines"))
    voice_id = resolve_voice(ctx, args.voice, args.language)
    fmt = ctx.api_format(args.format, "wav")
    settings = settings_from(args)
    workdir = ctx.workdir("piece")
    language = args.language if model != "eleven_multilingual_v2" else None
    result = api.text_to_speech_timed(ctx.client, voice_id, full, model, fmt, language, settings, args.seed, None, None)
    raw = workdir / "take_raw.wav"
    audio.write_api_audio(result.audio, fmt, raw, workdir / "decode", 1)
    if result.alignment is None:
        raise CliError("the API returned no alignment for the take; cannot cut it at the pauses")
    lufs = args.lufs if args.lufs is not None else ctx.config.get("default_lufs")
    take = workdir / "take.wav"
    audio.decode_to_wav(raw, take, ctx.sample_rate, ctx.config.channels, lufs)
    spans = script.locate_spans("".join(result.alignment.characters), [line.text for line in lines])
    threshold = ctx.config.get("trim_threshold_db")
    clips = audio.cut_lines_by_alignment(take, spans, result.alignment.starts, result.alignment.ends, threshold, ctx.sample_rate, ctx.config.channels, workdir / "lines")
    for line, clip in zip(lines, clips):
        audio.check_not_truncated(clip, ctx.config.get("truncation_db"), f"line {line.number} ({line.text[:40]!r})", args.allow_truncated)
    by_line = dict(zip((line.number for line in lines), clips))
    join_items: list[audio.JoinItem] = []
    ordinal = 0
    for item in items:
        if isinstance(item, script.Pause):
            join_items.append(audio.JoinItem(silence=item.seconds))
        else:
            ordinal += 1
            join_items.append(audio.JoinItem(file=str(by_line[item.number]), label=f"{ordinal} {item.text}"))
    timing = audio.join(join_items, out, workdir / "join", threshold, ctx.config.get("trim_margin_ms"), True, None, ctx.sample_rate, ctx.config.channels)
    data = json.loads(timing.to_json())
    data.update({"model": model, "seed": args.seed, "voice_id": voice_id, "take_lufs": lufs, "lines": [line.text for line in lines]})
    sidecar_path(out).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.clips_dir:
        keep_clips(ctx, args.clips_dir, lines, clips, voice_id, model, args.seed)
    report_saved(out)
    say(f"timing: {sidecar_path(out)} ({len(timing.clips)} lines, {len(timing.gaps)} gaps, {timing.duration:.3f} s)")
    verifiable = [g for g in timing.gaps if g.verifiable]
    if verifiable:
        check_and_report(ctx, out, [g.expected for g in verifiable], positions=[(g.start, g.end) for g in verifiable])


def keep_clips(ctx: Context, clips_dir: str, lines: list[script.Line], clips: list[Path], voice_id: str, model: str, seed: int | None) -> None:
    target_dir = ctx.resolve_out(clips_dir, "--clips-dir")
    target_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, (line, clip) in enumerate(zip(lines, clips), start=1):
        target = target_dir / f"{index:03d}.wav"
        shutil.copyfile(clip, target)
        entries.append({
            "id": f"{index:03d}", "text": line.text, "file": target.name, "duration": round(audio.probe(target).duration, 3),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "voice_id": voice_id, "model": model, "seed": seed,
        })
    manifest = target_dir / "manifest.json"
    manifest.write_text(json.dumps({"sample_rate": ctx.sample_rate, "channels": ctx.config.channels, "source": "one take, cut at the pauses", "clips": entries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_saved(manifest)
