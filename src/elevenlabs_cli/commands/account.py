"""``account`` (subscription and credits) and ``models`` (live model list)."""

from __future__ import annotations

import argparse
from typing import Any

from .. import client as api
from ..common import Context, emit, iso_from_unix, table


def register(subparsers: argparse._SubParsersAction) -> None:
    account = subparsers.add_parser("account", help="tier, credits used and limit, reset date, voice slots (free call)")
    account.set_defaults(func=run_account)
    models = subparsers.add_parser("models", help="models the account can use, with character limits (free call)")
    models.add_argument("--diff", action="store_true", help="compare the live list with the CLI's model table: new, gone, changed limits")
    models.set_defaults(func=run_models)


def run_account(args: Any) -> None:
    ctx = Context(args)
    sub = api.subscription(ctx.client)
    used = sub["character_count"]
    limit = sub["character_limit"]
    summary = {
        "tier": sub["tier"],
        "status": sub["status"],
        "credits_used": used,
        "credits_limit": limit,
        "credits_remaining": limit - used,
        "resets_at": iso_from_unix(sub.get("next_character_count_reset_unix")),
        "voice_slots_used": sub["voice_slots_used"],
        "voice_slots_limit": sub["voice_limit"],
        "instant_cloning": sub["can_use_instant_voice_cloning"],
        "professional_cloning": sub["can_use_professional_voice_cloning"],
    }
    text = "\n".join(f"{key:<22} {value}" for key, value in summary.items())
    emit(ctx, summary, text)


def diff_models(live: list[dict[str, Any]], table: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(model, kind, detail) rows: 'new' live models the table lacks, 'gone' table entries the API no longer lists,
    'limit' where the character limit differs."""
    rows: list[tuple[str, str, str]] = []
    live_by_id = {m["model_id"]: m for m in live}
    for model_id, m in live_by_id.items():
        if model_id not in table:
            rows.append((model_id, "new", f"{m['name']}, max {m['maximum_text_length_per_request']} chars"))
        elif m["maximum_text_length_per_request"] != table[model_id].max_chars:
            rows.append((model_id, "limit", f"API {m['maximum_text_length_per_request']}, table {table[model_id].max_chars}"))
    for model_id in table:
        if model_id not in live_by_id:
            rows.append((model_id, "gone", "not in the live list"))
    return rows


def run_models(args: Any) -> None:
    ctx = Context(args)
    models = [m for m in api.models(ctx.client) if m["can_do_text_to_speech"]]
    if args.diff:
        from ..cost import MODELS

        rows = diff_models(models, MODELS)
        emit(ctx, [{"model": r[0], "kind": r[1], "detail": r[2]} for r in rows], table(rows, ("model", "kind", "detail")) if rows else "model table matches the live list")
        return
    rows = [
        (
            m["model_id"],
            m["name"],
            m["maximum_text_length_per_request"],
            ", ".join(sorted({lang["language_id"] for lang in (m["languages"] or [])})),
        )
        for m in models
    ]
    emit(ctx, models, table(rows, ("model_id", "name", "max_chars", "languages")))
