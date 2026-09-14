"""``isolate``: remove background noise, keep the voice."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, report_saved
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("isolate", help="remove background noise from a recording")
    parser.add_argument("file")
    parser.add_argument("--out")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise CliError(f"file not found: {path}")
    out = ctx.resolve_out(args.out)
    duration = audio.probe(path).duration
    confirm_spend(ctx, Spend(None, None, f"isolating {duration / 60:.1f} min of audio: billed per minute of audio"))
    data = api.isolate(ctx.client, path)
    audio.decode_api_audio(data, "mp3_44100_128", out, ctx.workdir("isolate"))
    report_saved(out)
