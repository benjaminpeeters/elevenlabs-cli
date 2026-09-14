"""``verify``: measure every gap of a file against a spec or timing sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .. import audio
from ..common import Context, emit, table
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("verify", help="check that the silences in a file match the spec (non-zero exit on mismatch)")
    parser.add_argument("audio", help="the assembled file")
    parser.add_argument("spec", help="a .timing.json sidecar written by join, or a join spec file")
    parser.add_argument("--tolerance-ms", type=int, help="default: config verify_tolerance_ms")
    parser.set_defaults(func=run)


def expected_from(spec: Path) -> list[float]:
    if not spec.is_file():
        raise CliError(f"spec not found: {spec}")
    if spec.suffix == ".json":
        data = json.loads(spec.read_text(encoding="utf-8"))
        gaps = data.get("gaps")
        if not isinstance(gaps, list):
            raise CliError(f"{spec} has no 'gaps' list; is it a timing sidecar written by join?")
        return [float(g["expected"]) for g in gaps]
    items = audio.parse_spec_lines(spec.read_text(encoding="utf-8").splitlines(), spec.parent)
    return [item.silence for item in items if item.silence is not None]


def check_and_report(ctx: Context, path: Path, expected: list[float], tolerance_ms: int | None = None) -> None:
    tolerance = tolerance_ms if tolerance_ms is not None else ctx.config.get("verify_tolerance_ms")
    checks = audio.verify_gaps(path, expected, ctx.config.get("trim_threshold_db"), tolerance)
    rows = [
        (c.index + 1, f"{c.expected:.3f}", "missing" if c.measured is None else f"{c.measured:.3f}",
         "" if c.measured is None else f"{(c.measured - c.expected) * 1000:+.1f} ms", "ok" if c.ok else "MISMATCH")
        for c in checks
    ]
    emit(ctx, [c.__dict__ for c in checks], table(rows, ("gap", "expected s", "measured s", "delta", "status")))
    bad = [c for c in checks if not c.ok]
    if bad:
        raise CliError(f"{len(bad)} gap(s) off by more than {tolerance} ms in {path}")


def run(args: Any) -> None:
    ctx = Context(args)
    path = Path(args.audio).expanduser()
    check_and_report(ctx, path, expected_from(Path(args.spec).expanduser()), args.tolerance_ms)
