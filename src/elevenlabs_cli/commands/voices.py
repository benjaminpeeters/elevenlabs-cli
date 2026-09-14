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

    audition = sub.add_parser(
        "audition",
        help="render candidate voices on a lab protocol with a settings grid; trials go to the lab",
        description="Protocols live in <config dir>/lab/protocols/<use-case>/<isolation|longform|contrast>.txt. "
        "The contrast protocol is a dialogue: 'Candidate:' lines by the voice under test, 'Partner:' lines by the config's reference_partner. "
        "Every trial is saved as samples/<voice-id>/<trial-id>.m4a plus a JSON with settings and measurements; rate them with 'voices rate'.",
    )
    audition.add_argument("--use-case", dest="use_case", required=True, help="e.g. en-dialogue-elderly")
    audition.add_argument("--protocol", required=True, help="isolation | longform | contrast")
    audition.add_argument("--voices", nargs="+", required=True, help="voice ids, names or aliases (must be in the account)")
    audition.add_argument("--grid", choices=("single", "default", "wide"), default="single", help="single: v3 0.35 + v2 0.5; default: v3 x3 + v2 x4; wide: adds v3 1.0 and v2 speed 0.85")
    audition.add_argument("--models", default="eleven_v3,eleven_multilingual_v2", help="comma-separated subset of the grid's models")
    audition.add_argument("--language")
    audition.add_argument("--lufs", type=float, default=-18.0, help="per-clip loudness of the samples (comparable listening)")
    audition.add_argument("--dry-run", action="store_true")
    audition.set_defaults(func=run_audition)

    rate = sub.add_parser("rate", help="record a verdict (1..5) and a note on a trial")
    rate.add_argument("trial_id")
    rate.add_argument("verdict", type=int)
    rate.add_argument("--note", default="")
    rate.set_defaults(func=run_rate)

    shortlist = sub.add_parser("shortlist", help="best-rated voices for a use case, from the lab")
    shortlist.add_argument("--use-case", dest="use_case", required=True)
    shortlist.add_argument("--language")
    shortlist.set_defaults(func=run_shortlist)

    index = sub.add_parser("index", help="rebuild the lab index from the trial files")
    index.set_defaults(func=run_index)


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
    fmt = "mp3_44100_128"  # samples are for listening, not for the pipeline
    for name in args.voices:
        voice_id = resolve_voice(ctx, name, args.language)
        result = api.text_to_speech(ctx.client, voice_id, text, model, fmt, args.language, api.Settings(), None, None, None, None)
        target = out_dir / f"{voice_id}.mp3"
        audio.write_api_audio(result.audio, fmt, target, ctx.workdir("sample"), 1)
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
    say(f"saved: {target}")  # stderr, so --json output stays parseable


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


# --- the lab: auditions and ratings ---------------------------------------------------

GRIDS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "single": [
        ("eleven_v3", {"stability": 0.35}),
        ("eleven_multilingual_v2", {"stability": 0.5, "speed": 1.0}),
    ],
    "default": [
        ("eleven_v3", {"stability": 0.0, "tag": "warmly"}),
        ("eleven_v3", {"stability": 0.35}),
        ("eleven_v3", {"stability": 0.6}),
        ("eleven_multilingual_v2", {"stability": 0.35, "speed": 1.0}),
        ("eleven_multilingual_v2", {"stability": 0.5, "speed": 1.0}),
        ("eleven_multilingual_v2", {"stability": 0.35, "speed": 0.9}),
        ("eleven_multilingual_v2", {"stability": 0.5, "speed": 0.9}),
    ],
}
GRIDS["wide"] = GRIDS["default"] + [("eleven_v3", {"stability": 1.0}), ("eleven_multilingual_v2", {"stability": 0.5, "speed": 0.85})]


def settings_of(model: str, grid_settings: dict[str, Any]) -> api.Settings:
    if model == "eleven_v3":
        return api.Settings(stability=grid_settings.get("stability"))
    return api.Settings(stability=grid_settings.get("stability"), speed=grid_settings.get("speed"))


def run_audition(args: Any) -> None:
    from .. import lab
    from ..cost import billable_chars, estimate_credits
    from .dialogue import SpeakerProfile, render_dialogue

    ctx = Context(args)
    lab_root = lab.lab_dir(ctx.config.path)
    protocol = lab.load_protocol(lab_root, args.use_case, args.protocol)
    wanted = [m.strip() for m in args.models.split(",") if m.strip()]
    grid = [(m, st) for m, st in GRIDS[args.grid] if m in wanted]
    if not grid:
        raise CliError(f"no grid entries for models {wanted}; grid '{args.grid}' covers {sorted({m for m, _ in GRIDS[args.grid]})}")
    mine = {v["voice_id"]: v for v in api.my_voices(ctx.client)}
    candidates = []
    for name in args.voices:
        voice_id = resolve_voice(ctx, name, args.language)
        if voice_id not in mine:
            raise CliError(f"voice {voice_id} is not in the account; add it first (voices add <id> --owner <public_owner_id> --name <name>)")
        candidates.append(mine[voice_id])
    is_contrast = args.protocol == "contrast"
    partner = ctx.config.get("reference_partner")
    if is_contrast and not partner:
        raise CliError("the contrast protocol needs config reference_partner (a voice id)")
    chars = billable_chars(protocol.text)
    per_trial = {m: estimate_credits(protocol.text, m) for m, _ in grid}
    total = sum(per_trial[m] for m, _ in grid) * len(candidates)
    say(f"protocol {protocol.id}: {chars} characters; {len(candidates)} voice(s) x {len(grid)} setting(s) = {len(candidates) * len(grid)} trials, about {total:g} credits")
    if args.dry_run:
        for v in candidates:
            for model, st in grid:
                print(f"{v['name']:<40} {model:<24} {lab.settings_slug(model, st)}")
        return
    confirm_spend(ctx, Spend(chars * len(candidates) * len(grid), total, f"audition {args.use_case}/{args.protocol}, grid {args.grid}"))
    from datetime import date
    import elevenlabs as sdk

    today = date.today().isoformat()
    fmt = ctx.api_format(None, "wav")
    for v in candidates:
        for model, st in grid:
            trial_id = lab.make_trial_id(today, args.use_case, args.protocol, model, st)
            audio_path, _ = lab.trial_paths(lab_root, v["voice_id"], trial_id)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            text = protocol.text
            if st.get("tag"):
                text = "\n".join(f"[{st['tag']}] {line}" if line.strip() else line for line in text.splitlines())
            workdir = ctx.workdir("audition")
            if is_contrast:
                profiles = {"Candidate": SpeakerProfile("Candidate", v["voice_id"], "", model, settings_of(model, st)), "Partner": SpeakerProfile("Partner", partner, "", None, api.Settings())}
                timing = render_dialogue(ctx, text, profiles, "per-turn", audio_path, fmt=fmt, language=args.language, model=None, stability=None, seed=7, lufs=args.lufs, allow_truncated=True)
                duration = timing.duration
            else:
                paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
                clips = []
                for n, paragraph in enumerate(paragraphs):
                    r = api.text_to_speech(ctx.client, v["voice_id"], paragraph, model, fmt, args.language if model != "eleven_multilingual_v2" else None, settings_of(model, st), 7, None, None, None)
                    raw = workdir / f"{n:02d}.wav"
                    audio.write_api_audio(r.audio, fmt, raw, workdir / "decode", 1)
                    audio.check_not_truncated(raw, ctx.config.get("truncation_db"), f"{v['name']} paragraph {n + 1}", True)
                    clips.append(audio.JoinItem(file=str(raw)))
                    if n + 1 < len(paragraphs):
                        clips.append(audio.JoinItem(silence=0.8))
                timing = audio.join(clips, audio_path, workdir / "join", ctx.config.get("trim_threshold_db"), ctx.config.get("trim_margin_ms"), True, args.lufs, ctx.sample_rate, None)
                duration = timing.duration
            loud = audio.loudness(audio_path)
            tail = audio.tail_level(audio_path)
            trial = lab.Trial(
                trial_id, v["voice_id"], v["name"], dict(v.get("labels") or {}), args.language or (v.get("labels") or {}).get("language"),
                args.use_case, args.protocol, protocol.id, model, sdk.__version__, st, 7, today, audio_path.name,
                {"duration_s": round(duration, 2), "integrated_lufs": loud.integrated_lufs, "true_peak_dbtp": loud.true_peak_dbtp,
                 "loudness_range_lu": loud.loudness_range, "chars_per_second": round(chars / duration, 2), "tail_peak_db": round(tail.tail_peak_db, 1)},
            )
            lab.save_trial(lab_root, trial)
            print(f"saved: {audio_path}  ({trial_id})")
    lab.rebuild_index(lab_root)
    say(f"rate with: elevenlabs-cli voices rate <trial-id> <1..5> --note '...'")


def run_rate(args: Any) -> None:
    from .. import lab

    ctx = Context(args)
    if not 1 <= args.verdict <= 5:
        raise CliError("verdict must be 1..5")
    lab_root = lab.lab_dir(ctx.config.path)
    path, trial = lab.find_trial(lab_root, args.trial_id)
    trial.verdict = args.verdict
    trial.note = args.note
    path.write_text(trial.to_json(), encoding="utf-8")
    lab.rebuild_index(lab_root)
    print(f"{trial.voice_name} / {trial.trial_id}: {args.verdict}/5 {args.note}")


def run_shortlist(args: Any) -> None:
    from .. import lab

    ctx = Context(args)
    rows = lab.shortlist(lab.lab_dir(ctx.config.path), args.use_case, args.language)
    emit(ctx, rows, table([(r["voice_id"], r["name"], r["verdict"], r["protocols_rated"], ", ".join(f"{k} {v}" for k, v in r["per_protocol"].items())) for r in rows], ("voice_id", "name", "best", "protocols", "per protocol")) if rows else f"no rated trials for {args.use_case}")


def run_index(args: Any) -> None:
    from .. import lab

    ctx = Context(args)
    index = lab.rebuild_index(lab.lab_dir(ctx.config.path))
    print(f"{len(index['voices'])} voice(s), {sum(len(v['trials']) for v in index['voices'].values())} trial(s)")
