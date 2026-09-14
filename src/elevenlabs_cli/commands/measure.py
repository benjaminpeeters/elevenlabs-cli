"""``measure``: the numbers behind a complaint: duration, rate, layout, loudness, gaps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .. import audio
from ..common import Context, emit
from ..errors import CliError
from .verify import check_and_report, expected_from


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("measure", help="duration, sample rate, channels, loudness (LUFS, true peak, range) and, with --timing, the gap table")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--timing", help="a .timing.json sidecar to check the gaps of the (single) file against")
    parser.add_argument("--text", help="the spoken text, to report characters per second")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    if args.timing and len(args.files) != 1:
        raise CliError("--timing checks one file")
    rows: list[dict[str, Any]] = []
    for name in args.files:
        path = Path(name).expanduser()
        if not path.is_file():
            raise CliError(f"file not found: {path}")
        info = audio.probe(path)
        loud = audio.loudness(path)
        row: dict[str, Any] = {
            "file": str(path),
            "duration_s": round(info.duration, 3),
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "integrated_lufs": loud.integrated_lufs,
            "true_peak_dbtp": loud.true_peak_dbtp,
            "loudness_range_lu": loud.loudness_range,
        }
        if args.text:
            row["chars_per_second"] = round(len(args.text) / info.duration, 2)
        rows.append(row)
    text = "\n\n".join("\n".join(f"{key:<20} {value}" for key, value in row.items()) for row in rows)
    emit(ctx, rows, text)
    if args.timing:
        sidecar = Path(args.timing).expanduser()
        expected, positions = expected_from(sidecar)
        data = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.suffix == ".json" else {}
        for o in data.get("overlaps", []):
            print(f"overlap after clip {o['after'] + 1}: {o['expected']:.3f} s (voiced {o['start']:.3f}..{o['end']:.3f})")
        if data.get("headroom_gain_db"):
            print(f"headroom gain applied: {data['headroom_gain_db']:+.1f} dB")
        if expected:
            check_and_report(ctx, Path(args.files[0]).expanduser(), expected, None, positions)
        else:
            print("no silences to verify")
