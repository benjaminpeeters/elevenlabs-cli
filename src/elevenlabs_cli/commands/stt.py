"""``stt``: transcription with optional diarization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .. import audio
from .. import client as api
from ..common import Context, Spend, confirm_spend
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("stt", help="transcribe an audio or video file")
    parser.add_argument("file")
    parser.add_argument("--model", default="scribe_v1", help="scribe_v1 (default) or scribe_v1_experimental")
    parser.add_argument("--language", help="ISO 639-1 code; omit to auto-detect")
    parser.add_argument("--diarize", action="store_true", help="label speakers")
    parser.add_argument("--speakers", type=int, help="expected number of speakers")
    parser.add_argument("--audio-events", dest="audio_events", action="store_true", help="tag laughter, music, ...")
    parser.add_argument("--out", help="write the text (or JSON with --json) here instead of stdout")
    parser.set_defaults(func=run)


def run(args: Any) -> None:
    ctx = Context(args)
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise CliError(f"file not found: {path}")
    duration = audio.probe(path).duration
    confirm_spend(ctx, Spend(None, None, f"transcribing {duration / 60:.1f} min of audio: billed per hour of audio"))
    result = api.speech_to_text(ctx.client, path, args.model, args.language, args.diarize, args.speakers, args.audio_events)
    text = result["text"] if not args.diarize else diarized_text(result)
    output = json.dumps(result, indent=2, ensure_ascii=False) if ctx.json else text
    if args.out:
        target = ctx.resolve_out(args.out)
        target.write_text(output + "\n", encoding="utf-8")
        print(f"saved: {target}")
    else:
        print(output)


def diarized_text(result: dict[str, Any]) -> str:
    """One paragraph per speaker turn from the word list."""
    lines: list[str] = []
    speaker = None
    current: list[str] = []
    for word in result.get("words") or []:
        if word.get("type") == "spacing":
            continue
        if word.get("speaker_id") != speaker:
            if current:
                lines.append(f"{speaker}: {' '.join(current)}")
            speaker = word.get("speaker_id")
            current = []
        current.append(word["text"])
    if current:
        lines.append(f"{speaker}: {' '.join(current)}")
    return "\n".join(lines) if lines else result["text"]
