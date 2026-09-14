"""Entry point: one argparse tree, subcommands registered from a table."""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from . import __version__
from .commands import account, clips, config_cmd, dialogue, isolate, join, sfx, stt, sts, tts, verify, voice, voices
from .errors import CliError

REGISTRARS: list[Callable[[argparse._SubParsersAction], None]] = [
    config_cmd.register,
    account.register,
    voices.register,
    tts.register,
    join.register,
    verify.register,
    clips.register,
    dialogue.register,
    sfx.register,
    stt.register,
    isolate.register,
    sts.register,
    voice.register,
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elevenlabs-cli",
        description="ElevenLabs from the command line: speech, dialogue, sound effects, transcription, voices, "
        "plus exact silence assembly and verification with ffmpeg.",
    )
    parser.add_argument("--version", action="version", version=f"elevenlabs-cli {__version__}")
    parser.add_argument("--json", action="store_true", help="machine-readable output where the command supports it")
    parser.add_argument("--yes", action="store_true", help="skip the spend confirmation")
    parser.add_argument("--config", metavar="PATH", help="config file (default: $ELEVENLABS_CLI_CONFIG or ~/.config/elevenlabs-cli/config.json)")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for register in REGISTRARS:
        register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except CliError as exc:
        print(f"elevenlabs-cli: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("elevenlabs-cli: interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
