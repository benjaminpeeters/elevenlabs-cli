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


def run_models(args: Any) -> None:
    ctx = Context(args)
    models = [m for m in api.models(ctx.client) if m["can_do_text_to_speech"]]
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
