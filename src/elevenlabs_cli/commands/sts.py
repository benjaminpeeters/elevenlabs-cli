"""``sts``: re-render a recording in another voice."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, report_saved, resolve_voice
from ..errors import CliError
from .tts import add_voice_settings, settings_from


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("sts", help="speech to speech: the same performance in another voice")
    parser.add_argument("file")
    parser.add_argument("--voice", required=True)
    parser.add_argument("--language", help="selects the alias language")
    parser.add_argument("--model", default="eleven_multilingual_sts_v2")
    parser.add_argument("--format")
    add_voice_settings(parser)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--remove-noise", dest="remove_noise", action="store_true")
    parser.add_argument("--out")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise CliError(f"file not found: {path}")
    out = ctx.resolve_out(args.out)
    duration = audio.probe(path).duration
    confirm_spend(ctx, Spend(None, None, f"converting {duration:.1f} s of audio: billed by input duration"))
    voice_id = resolve_voice(ctx, args.voice, args.language)
    fmt = ctx.api_format(args.format, "wav")
    data = api.speech_to_speech(ctx.client, path, voice_id, args.model, fmt, settings_from(args), args.seed, args.remove_noise)
    audio.write_api_audio(data, fmt, out, ctx.workdir("sts"), 1)
    report_saved(out)
