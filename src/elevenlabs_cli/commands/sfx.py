"""``sfx``: a sound effect from a text description."""

from __future__ import annotations

import argparse
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, report_saved


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("sfx", help="sound effect from a description")
    parser.add_argument("--text", required=True)
    parser.add_argument("--duration", type=float, help="seconds (0.5..30); default lets the model choose")
    parser.add_argument("--prompt-influence", dest="prompt_influence", type=float, help="0..1")
    parser.add_argument("--loop", action="store_true", help="make it loopable")
    parser.add_argument("--format")
    parser.add_argument("--out")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    out = ctx.resolve_out(args.out)
    length = "model-chosen length" if args.duration is None else f"{args.duration:g} s"
    confirm_spend(ctx, Spend(None, None, f"sound effect, {length}: billed per generation, not per character"))
    fmt = ctx.output_format(args.format)
    data = api.sound_effect(ctx.client, args.text, args.duration, args.prompt_influence, fmt, args.loop)
    audio.decode_api_audio(data, fmt, out, ctx.workdir("sfx"))
    report_saved(out)
