"""Helpers shared by the commands: context, spend confirmation, voice resolution, output."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import client as api
from .config import API_RATES, Config
from .errors import CliError

NEVER_ASK = 100000  # confirm_above_chars at or above this never asks, even for per-call billing

OUTPUT_FORMATS = (
    "mp3_22050_32", "mp3_24000_48", "mp3_44100_32", "mp3_44100_64", "mp3_44100_96", "mp3_44100_128", "mp3_44100_192",
    "opus_48000_32", "opus_48000_64", "opus_48000_96", "opus_48000_128", "opus_48000_192",
    "pcm_8000", "pcm_16000", "pcm_22050", "pcm_24000", "pcm_32000", "pcm_44100", "pcm_48000",
    "wav_8000", "wav_16000", "wav_22050", "wav_24000", "wav_32000", "wav_44100", "wav_48000",
    "ulaw_8000", "alaw_8000",
)


class Context:
    """What every command needs: the parsed args, the config, a lazy client."""

    def __init__(self, args: Any) -> None:
        self.args = args
        self.config = Config.load(args.config)
        self._client: Any = None

    @property
    def json(self) -> bool:
        return bool(self.args.json)

    @property
    def yes(self) -> bool:
        return bool(self.args.yes)

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = api.make_client(self.config.api_key())
        return self._client

    def workdir(self, label: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=f"elevenlabs-cli-{label}-"))

    def resolve_out(self, out: str | None, what: str = "--out") -> Path:
        """An explicit output path; a relative one lands under ``output_dir`` or, when unset, the current directory."""
        if not out:
            base = self.config.output_dir or Path.cwd()
            raise CliError(f"{what} is required (a relative path would land under {base})")
        path = Path(out).expanduser()
        if not path.is_absolute():
            path = (self.config.output_dir or Path.cwd()) / path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CliError(f"cannot write to {path.parent}: {exc.strerror}; choose another {what} or make that folder writable") from exc
        return path.resolve()

    def api_format(self, override: str | None, family: str) -> str:
        """The API output format: ``--format`` as given, else lossless at the working rate.

        ``family`` is ``wav`` for endpoints that offer WAV (speech, dialogue,
        speech-to-speech) and ``pcm`` for those that only offer raw samples
        (sound effects, music); raw samples are wrapped into a WAV on write.
        """
        if override is not None:
            if override not in OUTPUT_FORMATS:
                raise CliError(f"unknown output format '{override}' (known: {', '.join(OUTPUT_FORMATS)})")
            return override
        return f"{family}_{self.config.sample_rate}"

    @property
    def sample_rate(self) -> int:
        return self.config.sample_rate

    def rate(self, override: int | None) -> int:
        if override is None:
            return self.config.sample_rate
        if override not in API_RATES:
            raise CliError(f"--rate must be one of {', '.join(str(r) for r in API_RATES)}, got {override}")
        return override

    def model(self, override: str | None) -> str:
        return override or self.config.get("default_model")

    def model_for_text(self, override: str | None, text: str) -> tuple[str, bool]:
        """The model for one utterance: an explicit --model always wins; otherwise short lines use
        ``short_line_model`` because v3 randomly cuts their tail (finding v3-short-utterance-truncation).
        Returns (model, switched)."""
        if override is not None:
            return override, False
        short_model = self.config.get("short_line_model")
        if short_model and len(text.strip()) < self.config.get("short_line_chars") and short_model != self.config.get("default_model"):
            return short_model, True
        return self.config.get("default_model"), False


@dataclass(frozen=True)
class Spend:
    """What a command is about to bill: characters when known, otherwise a note."""

    chars: int | None
    credits: float | None
    note: str


def confirm_spend(ctx: Context, spend: Spend) -> None:
    """Print the estimate; refuse above the threshold unless ``--yes`` or the user agrees.

    Character-billed calls compare ``chars`` with ``confirm_above_chars``; calls
    billed per generation or per second ask whenever the threshold is below
    ``NEVER_ASK``. Without a terminal the command refuses instead of asking.
    """
    threshold = ctx.config.get("confirm_above_chars")
    if spend.chars is not None:
        credits = "" if spend.credits is None else f", about {spend.credits:g} credits"
        say(f"{spend.chars} characters{credits} ({spend.note})")
        needs = spend.chars > threshold
    else:
        say(spend.note)
        needs = threshold < NEVER_ASK
    if not needs or ctx.yes:
        return
    if not sys.stdin.isatty():
        raise CliError(
            f"generation not confirmed: above the confirm_above_chars threshold ({threshold}) and no terminal to ask. "
            "Re-run with --yes, or raise the threshold with 'elevenlabs-cli config set confirm_above_chars N'."
        )
    answer = input("Proceed? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise CliError("cancelled", exit_code=2)


def say(message: str) -> None:
    """Progress and estimates go to stderr so stdout stays machine-readable."""
    print(message, file=sys.stderr)


def emit(ctx: Context, data: Any, text: str) -> None:
    if ctx.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def report_saved(path: Path) -> None:
    print(f"saved: {path}")


def resolve_voice(ctx: Context, name: str, language: str | None) -> str:
    """Alias from the config, then the exact name of one of the account's voices, then a voice id.

    Never guesses: an unknown name is an error that says how to search.
    """
    if not name:
        raise CliError("--voice is required")
    alias = ctx.config.resolve_alias(name, language)
    if alias is not None:
        return alias
    mine = api.my_voices(ctx.client)
    by_name = [v for v in mine if v["name"] == name]
    if len(by_name) == 1:
        return by_name[0]["voice_id"]
    if len(by_name) > 1:
        ids = ", ".join(v["voice_id"] for v in by_name)
        raise CliError(f"several voices are named '{name}' ({ids}); pass the voice id")
    by_id = [v for v in mine if v["voice_id"] == name]
    if by_id:
        return name
    raise CliError(
        f"no voice alias, name or id matches '{name}'. "
        "Use 'elevenlabs-cli voices list' for your voices, 'voices search' for the library, "
        "or add an alias with 'config set voices.<lang>.<alias> <voice id>'."
    )


def read_text_input(text: str | None, file: str | None) -> str:
    if (text is None) == (file is None):
        raise CliError("pass exactly one of --text or --file")
    if text is not None:
        if not text.strip():
            raise CliError("--text is empty")
        return text
    path = Path(file).expanduser()
    if not path.is_file():
        raise CliError(f"text file not found: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise CliError(f"text file is empty: {path}")
    return content


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    cells = [[str(c) if c is not None else "" for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def fmt(row: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
    lines = [fmt(headers), fmt(["-" * w for w in widths])]
    lines.extend(fmt(row) for row in cells)
    return "\n".join(lines)


def iso_from_unix(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
