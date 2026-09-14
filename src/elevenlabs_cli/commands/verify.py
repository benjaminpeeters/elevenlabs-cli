"""``verify``: measure every gap of a file against a spec or timing sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .. import audio
from ..common import Context, emit, say, table
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("verify", help="check that the silences in a file match the spec (non-zero exit on mismatch)")
    parser.add_argument("audio", help="the assembled file")
    parser.add_argument("spec", help="a .timing.json sidecar written by join, or a join spec file")
    parser.add_argument("--tolerance-ms", type=int, help="default: config verify_tolerance_ms")
    parser.set_defaults(func=run)


def expected_from(spec: Path) -> tuple[list[float], list[tuple[float, float]] | None]:
    if not spec.is_file():
        raise CliError(f"spec not found: {spec}")
    if spec.suffix == ".json":
        data = json.loads(spec.read_text(encoding="utf-8"))
        gaps = data.get("gaps")
        if not isinstance(gaps, list):
            raise CliError(f"{spec} has no 'gaps' list; is it a timing sidecar written by join?")
        usable = [g for g in gaps if g.get("verifiable", True)]
        return [float(g["expected"]) for g in usable], [(float(g["start"]), float(g["end"])) for g in usable]
    items = audio.parse_spec_lines(spec.read_text(encoding="utf-8").splitlines(), spec.parent)
    return [item.silence for item in items if item.silence is not None], None


def check_and_report(ctx: Context, path: Path, expected: list[float], tolerance_ms: int | None = None, positions: list[tuple[float, float]] | None = None) -> None:
    if not expected:
        say("nothing to verify: no silences in the spec")
        return
    tolerance = tolerance_ms if tolerance_ms is not None else ctx.config.get("verify_tolerance_ms")
    if positions is None:
        say("no timing sidecar: silences are matched in order, so a long pause inside speech can shift the table; prefer <out>.timing.json")
    checks = audio.verify_gaps(path, expected, ctx.config.get("trim_threshold_db"), tolerance, positions)
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
    expected, positions = expected_from(Path(args.spec).expanduser())
    check_and_report(ctx, path, expected, args.tolerance_ms, positions)
