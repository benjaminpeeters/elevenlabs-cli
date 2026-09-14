"""``mix``: beds (rain, music, noise) under a finished piece, for exports."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import audio
from ..common import Context, report_saved, say
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "mix",
        help="lay beds under a joined piece (fades, loop, gain, optional ducking); the last step of an export",
        description="Each --bed is looped or cut to the speech length, faded in and out, set to its gain in dB "
        "(negative = under the speech), optionally ducked while speech is present, then summed. "
        "Run join and verify first: the mixed file has no measurable gaps any more. "
        "App content keeps beds separate; mix is for podcast, video and one-off exports.",
    )
    parser.add_argument("speech", help="the joined piece (the timing reference)")
    parser.add_argument("--bed", action="append", required=True, metavar="FILE[:GAIN_DB]", help="repeatable; e.g. rain.wav:-18 (default gain -18 dB)")
    parser.add_argument("--fade-in", dest="fade_in", type=float, default=3.0, help="seconds (default 3)")
    parser.add_argument("--fade-out", dest="fade_out", type=float, default=5.0, help="seconds (default 5)")
    parser.add_argument("--duck", type=float, help="drop the beds by this many dB while speech is present")
    parser.add_argument("--lufs", type=float, help="loudness target for the final mix (default: none)")
    parser.add_argument("--rate", type=int, help="output sample rate (default: config sample_rate)")
    parser.add_argument("--channels", type=int, help="1 or 2 (default: stereo as soon as an input is stereo)")
    parser.add_argument("--out", help="output file (.m4a for delivery, .wav or .flac for a master)")
    parser.set_defaults(func=run)


def parse_bed(text: str) -> audio.Bed:
    path, sep, gain = text.rpartition(":")
    if not sep or not path:
        return audio.Bed(str(Path(text).expanduser()), -18.0)
    try:
        return audio.Bed(str(Path(path).expanduser()), float(gain))
    except ValueError:
        return audio.Bed(str(Path(text).expanduser()), -18.0)


def run(args: Any) -> None:
    ctx = Context(args)
    speech = Path(args.speech).expanduser()
    if not speech.is_file():
        raise CliError(f"speech file not found: {speech}")
    beds = [parse_bed(b) for b in args.bed]
    for bed in beds:
        if not Path(bed.path).is_file():
            raise CliError(f"bed not found: {bed.path}")
    out = ctx.resolve_out(args.out)
    info = audio.mix(speech, beds, out, ctx.workdir("mix"), ctx.rate(args.rate), args.channels, args.fade_in, args.fade_out, args.duck, args.lufs)
    report_saved(out)
    say(f"{info.duration:.3f} s, {info.sample_rate} Hz, {'stereo' if info.channels == 2 else 'mono'}; beds: " + ", ".join(f"{Path(b.path).name} at {b.gain_db:+.0f} dB" for b in beds))
