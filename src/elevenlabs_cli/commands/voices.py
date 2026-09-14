"""``voices list|search|sample|add|get|delete``."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend, emit, report_saved, resolve_voice, say, table
from ..cost import billable_chars, estimate_credits
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("voices", help="list your voices, search the library, render samples, add or inspect a voice")
    sub = parser.add_subparsers(dest="voices_command", metavar="<action>", required=True)

    list_ = sub.add_parser("list", help="voices in your account (free call)")
    list_.add_argument("--search", help="filter by name or description")
    list_.set_defaults(func=run_list)

    search = sub.add_parser("search", help="search the shared voice library (free call)")
    search.add_argument("--language", help="ISO 639-1 code, e.g. en, fr, zh")
    search.add_argument("--gender", help="male | female | neutral")
    search.add_argument("--age", help="young | middle_aged | old")
    search.add_argument("--accent")
    search.add_argument(
        "--use-case", dest="use_case",
        help="API vocabulary: narrative_story, conversational, characters_animation, social_media, entertainment_tv, advertisement, informative_educational (for a theme such as meditation use --search)",
    )
    search.add_argument("--search", help="free text: matches names and descriptions, e.g. 'meditation', 'calm', 'audiobook'")
    search.add_argument("--category", help="professional | famous | high_quality | generated")
    search.add_argument("--featured", action="store_true")
    search.add_argument("--page-size", type=int, default=30)
    search.add_argument("--preview-dir", dest="preview_dir", help="also download every result's library preview (free) into this folder")
    search.set_defaults(func=run_search)

    sample = sub.add_parser("sample", help="render or download a sample per voice to a folder")
    sample.add_argument("voices", nargs="+", help="voice ids, names or aliases")
    sample.add_argument("--out-dir", required=True, help="folder for <voice id>.mp3 (relative: under output_dir)")
    sample.add_argument("--preview", action="store_true", help="download the voice's preview instead of rendering (free; voices in your account only, use 'voices search --preview-dir' for library voices)")
    sample.add_argument("--text", help="text to render (default: a 20-second calm paragraph)")
    sample.add_argument("--model")
    sample.add_argument("--language")
    sample.set_defaults(func=run_sample)

    add = sub.add_parser("add", help="add a library voice to your account")
    add.add_argument("voice_id")
    add.add_argument("--owner", required=True, help="public_owner_id shown by 'voices search'")
    add.add_argument("--name", required=True, help="name in your account")
    add.set_defaults(func=run_add)

    get = sub.add_parser("get", help="details of one of your voices")
    get.add_argument("voice", help="id, exact name or alias")
    get.add_argument("--language")
    get.set_defaults(func=run_get)

    delete = sub.add_parser("delete", help="remove a voice from your account")
    delete.add_argument("voice_id")
    delete.set_defaults(func=run_delete)


def labels_text(labels: dict[str, Any] | None) -> str:
    if not labels:
        return ""
    return ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))


def run_list(args: Any) -> None:
    ctx = Context(args)
    voices = api.my_voices(ctx.client, search=args.search)
    rows = [(v["voice_id"], v["name"], v.get("category") or "", labels_text(v.get("labels"))) for v in voices]
    emit(ctx, voices, table(rows, ("voice_id", "name", "category", "labels")))


def run_search(args: Any) -> None:
    ctx = Context(args)
    filters: dict[str, Any] = {"page_size": args.page_size}
    for key in ("language", "gender", "age", "accent", "use_case", "search", "category"):
        value = getattr(args, key)
        if value is not None:
            filters["use_cases" if key == "use_case" else key] = value
    if args.featured:
        filters["featured"] = True
    voices = api.library_voices(ctx.client, **filters)
    rows = [
        (
            v["voice_id"],
            v["public_owner_id"],
            v["name"],
            v.get("language") or "",
            v.get("gender") or "",
            v.get("age") or "",
            v.get("accent") or "",
            v.get("use_case") or "",
            v.get("descriptive") or "",
            v.get("cloned_by_count") or 0,
        )
        for v in voices
    ]
    headers = ("voice_id", "public_owner_id", "name", "lang", "gender", "age", "accent", "use_case", "style", "uses")
    emit(ctx, voices, table(rows, headers))
    sys.stdout.flush()
    if args.preview_dir:
        out_dir = ctx.resolve_out(args.preview_dir, "--preview-dir")
        out_dir.mkdir(parents=True, exist_ok=True)
        for v in voices:
            if not v.get("preview_url"):
                say(f"no preview for {v['voice_id']} ({v['name']})")
                continue
            download(v["preview_url"], out_dir / f"{slug(v['name'])}_{v['voice_id']}.mp3")
    elif not ctx.json and voices:
        say("add one with: elevenlabs-cli voices add <voice_id> --owner <public_owner_id> --name <name>; free previews: add --preview-dir <dir>")


def run_sample(args: Any) -> None:
    ctx = Context(args)
    out_dir = ctx.resolve_out(args.out_dir, "--out-dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.preview:
        for name in args.voices:
            voice_id = resolve_voice(ctx, name, args.language)
            download(preview_url(ctx, voice_id), out_dir / f"{voice_id}.mp3")
        return
    text = args.text or api.SAMPLE_TEXT
    model = ctx.model(args.model)
    chars = billable_chars(text)
    confirm_spend(ctx, Spend(chars * len(args.voices), estimate_credits(text, model) * len(args.voices), f"{len(args.voices)} sample(s) with {model}"))
    fmt = ctx.output_format(None)
    for name in args.voices:
        voice_id = resolve_voice(ctx, name, args.language)
        result = api.text_to_speech(ctx.client, voice_id, text, model, fmt, args.language, api.Settings(), None, None, None, None)
        target = out_dir / f"{voice_id}.mp3"
        audio.decode_api_audio(result.audio, fmt, target, ctx.workdir("sample"))
        report_saved(target)


def preview_url(ctx: Context, voice_id: str) -> str:
    """The preview of one of the account's voices."""
    url = api.get_voice(ctx.client, voice_id).get("preview_url")
    if not url:
        raise CliError(f"voice {voice_id} has no preview; render one with 'voices sample {voice_id} --out-dir <dir>'")
    return url


def download(url: str, target: Path) -> None:
    try:
        with urllib.request.urlopen(url) as response:
            target.write_bytes(response.read())
    except OSError as exc:
        raise CliError(f"could not download {url}: {exc}") from exc
    report_saved(target)


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:40]


def run_add(args: Any) -> None:
    ctx = Context(args)
    new_id = api.add_library_voice(ctx.client, args.owner, args.voice_id, args.name)
    print(f"added voice '{args.name}' as {new_id}")


def run_get(args: Any) -> None:
    ctx = Context(args)
    voice_id = resolve_voice(ctx, args.voice, args.language)
    voice = api.get_voice(ctx.client, voice_id)
    lines = [
        f"{'voice_id':<20} {voice['voice_id']}",
        f"{'name':<20} {voice['name']}",
        f"{'category':<20} {voice.get('category') or ''}",
        f"{'description':<20} {voice.get('description') or ''}",
        f"{'labels':<20} {labels_text(voice.get('labels'))}",
        f"{'languages':<20} {', '.join(v['language_id'] for v in (voice.get('verified_languages') or []))}",
        f"{'settings':<20} {voice.get('settings') or ''}",
        f"{'preview_url':<20} {voice.get('preview_url') or ''}",
    ]
    emit(ctx, voice, "\n".join(lines))


def run_delete(args: Any) -> None:
    ctx = Context(args)
    api.delete_voice(ctx.client, args.voice_id)
    print(f"deleted voice {args.voice_id}")
