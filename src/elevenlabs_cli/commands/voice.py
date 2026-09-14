"""``voice design|create|clone``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import client as api
from ..common import Context, Spend, confirm_spend, report_saved
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("voice", help="design a voice from a description, save a preview, or clone from samples")
    sub = parser.add_subparsers(dest="voice_command", metavar="<action>", required=True)

    design = sub.add_parser("design", help="generate previews from a description; pick one with 'voice create'")
    design.add_argument("--description", required=True, help="20..1000 characters")
    design.add_argument("--text", help="what the previews say (100..1000 characters); omitted = generated")
    design.add_argument("--model", help="eleven_multilingual_ttv_v2 or eleven_ttv_v3")
    design.add_argument("--seed", type=int)
    design.add_argument("--guidance", type=float, help="0..100, how closely to follow the description")
    design.add_argument("--loudness", type=float, help="-1..1")
    design.add_argument("--out-dir", required=True, help="folder for preview_<n>.mp3")
    design.set_defaults(func=run_design)

    create = sub.add_parser("create", help="save a designed preview as a voice")
    create.add_argument("--name", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--generated-id", dest="generated_id", required=True, help="generated_voice_id printed by 'voice design'")
    create.set_defaults(func=run_create)

    clone = sub.add_parser("clone", help="instant voice clone from sample recordings")
    clone.add_argument("--name", required=True)
    clone.add_argument("--sample", action="append", required=True, help="audio file; repeat for several")
    clone.add_argument("--description")
    clone.add_argument("--remove-noise", dest="remove_noise", action="store_true")
    clone.set_defaults(func=run_clone)


def run_design(args: Any) -> None:
    ctx = Context(args)
    out_dir = ctx.resolve_out(args.out_dir, "--out-dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    confirm_spend(ctx, Spend(None, None, "voice design: billed per call (several previews)"))
    previews = api.design_voice(ctx.client, args.description, args.text, args.model, args.seed, args.guidance, args.loudness)
    for index, preview in enumerate(previews, start=1):
        ext = ".mp3" if "mpeg" in preview.media_type or "mp3" in preview.media_type else ".bin"
        target = out_dir / f"preview_{index}{ext}"
        target.write_bytes(preview.audio)
        report_saved(target)
        print(f"  generated_voice_id: {preview.generated_voice_id}")
    print("save one with: elevenlabs-cli voice create --name <name> --description '<description>' --generated-id <id>")


def run_create(args: Any) -> None:
    ctx = Context(args)
    voice_id = api.create_designed_voice(ctx.client, args.name, args.description, args.generated_id)
    print(f"created voice '{args.name}' as {voice_id}")


def run_clone(args: Any) -> None:
    ctx = Context(args)
    samples = [Path(s).expanduser() for s in args.sample]
    missing = [str(s) for s in samples if not s.is_file()]
    if missing:
        raise CliError(f"sample file(s) not found: {', '.join(missing)}")
    confirm_spend(ctx, Spend(None, None, f"instant clone from {len(samples)} sample(s): uses one voice slot"))
    voice_id = api.clone_voice(ctx.client, args.name, samples, args.description, args.remove_noise)
    print(f"cloned voice '{args.name}' as {voice_id}")
