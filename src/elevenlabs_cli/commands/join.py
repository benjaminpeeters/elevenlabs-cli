"""``join``: trimmed clips plus exact silences into one file, with a timing sidecar."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import audio
from ..common import Context, report_saved, say
from ..errors import CliError
from .verify import check_and_report


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "join",
        help="assemble clips and exact silences into one file",
        description="Items are file paths, 'silence:<seconds>' (exact gap between voiced content) and 'overlap:<seconds>' "
        "(the next voice starts before the previous one ends); or a spec file with 'file <path> [fade=<s>]', 'silence <s>', 'overlap <s>' lines. "
        "Clips are trimmed to their voiced content first (unless --no-trim); every clip edge is faded a few milliseconds; the sum is kept under -1 dBFS.",
    )
    parser.add_argument("items", nargs="*", help="paths, silence:<seconds> and overlap:<seconds> entries, in order")
    parser.add_argument("--spec", help="spec file (paths relative to the spec's folder)")
    parser.add_argument("--out", help="output file (.wav, .m4a, .mp3, .opus, .flac)")
    parser.add_argument("--no-trim", dest="trim", action="store_false", help="keep the clips' own leading and trailing silence")
    parser.add_argument("--lufs", type=float, help="per-clip loudness target (default: config default_lufs; omit both for none)")
    parser.add_argument("--rate", type=int, help="output sample rate (default: config sample_rate)")
    parser.add_argument("--channels", type=int, help="1 or 2 (default: stereo as soon as one input is stereo)")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="skip measuring the gaps of the result")
    parser.set_defaults(func=run)


def load_items(args: Any) -> list[audio.JoinItem]:
    if args.spec and args.items:
        raise CliError("pass either --spec or positional items, not both")
    if args.spec:
        spec = Path(args.spec).expanduser()
        if not spec.is_file():
            raise CliError(f"spec file not found: {spec}")
        return audio.parse_spec_lines(spec.read_text(encoding="utf-8").splitlines(), spec.parent)
    if not args.items:
        raise CliError("nothing to join: pass items or --spec")
    return audio.parse_spec_args(args.items)


def sidecar_path(out: Path) -> Path:
    return out.with_suffix(".timing.json")


def run(args: Any) -> None:
    ctx = Context(args)
    items = load_items(args)
    out = ctx.resolve_out(args.out)
    lufs = args.lufs if args.lufs is not None else ctx.config.get("default_lufs")
    timing = audio.join(
        items, out, ctx.workdir("join"),
        ctx.config.get("trim_threshold_db"), ctx.config.get("trim_margin_ms"),
        do_trim=args.trim, lufs=lufs, sample_rate=ctx.rate(args.rate), channels=args.channels,
    )
    sidecar = sidecar_path(out)
    sidecar.write_text(timing.to_json() + "\n", encoding="utf-8")
    report_saved(out)
    extra = f", {len(timing.overlaps)} overlaps" if timing.overlaps else ""
    gain = f", headroom gain {timing.headroom_gain_db:+.1f} dB" if timing.headroom_gain_db else ""
    say(f"timing: {sidecar} ({len(timing.clips)} clips, {len(timing.gaps)} gaps{extra}, {timing.duration:.3f} s, {timing.sample_rate} Hz, {'stereo' if timing.channels == 2 else 'mono'}{gain})")
    verifiable = [g for g in timing.gaps if g.verifiable]
    if args.verify and verifiable:
        check_and_report(ctx, out, [g.expected for g in verifiable], positions=[(g.start, g.end) for g in verifiable])
