"""``noise``: coloured noise generated locally with ffmpeg (no credits)."""

from __future__ import annotations

import argparse
from typing import Any

from .. import audio
from ..common import Context, report_saved


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("noise", help="white, pink or brown (red) noise of an exact length, generated locally, free")
    parser.add_argument("--color", default="brown", help="white | pink | brown (= red) | blue | violet")
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--amplitude", type=float, default=0.5, help="0..1 (default 0.5)")
    parser.add_argument("--rate", type=int, help="sample rate (default: config sample_rate)")
    parser.add_argument("--channels", type=int, help="1 or 2 (default: config channels; stereo noise uses two independent generators)")
    parser.add_argument("--seed", type=int, help="fixed seed for a reproducible bed")
    parser.add_argument("--lufs", type=float, help="loudness target (default: none)")
    parser.add_argument("--fade-in", dest="fade_in", type=float, default=0.5, help="seconds (default 0.5)")
    parser.add_argument("--fade-out", dest="fade_out", type=float, default=0.5, help="seconds (default 0.5)")
    parser.add_argument("--highpass", type=float, help="Hz; default 40 for brown/red (removes the random walk's drift), none for other colours; 0 disables")
    parser.add_argument("--out", help="output file (.wav, .m4a, .mp3, .opus, .flac)")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    out = ctx.resolve_out(args.out)
    highpass = None if args.highpass is None else (args.highpass if args.highpass > 0 else 0.0)
    audio.noise(out, args.color, args.seconds, ctx.rate(args.rate), args.channels if args.channels is not None else ctx.config.channels, args.amplitude, args.seed, args.lufs, args.fade_in, args.fade_out, highpass)
    report_saved(out)
