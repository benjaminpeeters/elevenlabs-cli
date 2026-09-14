"""``music``: a composition from a prompt (Eleven Music), stereo at the working rate."""

from __future__ import annotations

import argparse
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, report_saved


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("music", help="compose music from a prompt (stereo)")
    parser.add_argument("--prompt", required=True, help="style, mood, instruments, tempo")
    parser.add_argument("--seconds", type=float, required=True, help="length, 10 s to 5 min")
    parser.add_argument("--instrumental", action="store_true", help="no vocals")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--format", help="default: pcm_<sample_rate>, wrapped into a stereo WAV")
    parser.add_argument("--out")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    out = ctx.resolve_out(args.out)
    confirm_spend(ctx, Spend(None, None, f"music, {args.seconds:g} s: billed per generation"))
    fmt = ctx.api_format(args.format, "pcm")
    data = api.music(ctx.client, args.prompt, args.seconds, fmt, args.instrumental, args.seed)
    audio.write_api_audio(data, fmt, out, ctx.workdir("music"), 2)
    report_saved(out)
