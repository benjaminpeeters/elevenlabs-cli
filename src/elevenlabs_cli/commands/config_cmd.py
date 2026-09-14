"""``config init|show|get|set``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..config import CONFIG_ENV, SCHEMA, Config, coerce, config_path, defaults, validate
from ..errors import CliError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("config", help="create, show or edit the config file")
    sub = parser.add_subparsers(dest="config_command", metavar="<action>", required=True)

    init = sub.add_parser("init", help="write a new config file (prompts for missing values on a terminal)")
    init.add_argument("--api-key-env", help="environment variable holding the key (default ELEVENLABS_API_KEY)")
    init.add_argument("--api-key-file", help="file holding the key (bare key, or KEY=value lines)")
    init.add_argument("--output-dir", help="folder for relative --out paths (default: none, the current directory)")
    init.add_argument("--default-model", help="model used when --model is absent (default eleven_v3)")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(func=run_init)

    upgrade = sub.add_parser("upgrade", help="add keys a newer CLI introduced, with their defaults; existing values untouched")
    upgrade.set_defaults(func=run_upgrade)

    show = sub.add_parser("show", help="print the config as JSON (the key itself is never shown)")
    show.set_defaults(func=run_show)

    get = sub.add_parser("get", help="print one value")
    get.add_argument("key", help="a top-level key, or voices.<language>.<alias>")
    get.set_defaults(func=run_get)

    set_ = sub.add_parser("set", help="set one value and save")
    set_.add_argument("key", help="a top-level key, or voices.<language>.<alias>")
    set_.add_argument("value", help="the new value ('null' clears an optional key or removes an alias)")
    set_.set_defaults(func=run_set)


def run_init(args: Any) -> None:
    path = config_path(args.config)
    if path.exists() and not args.force:
        raise CliError(f"config file already exists: {path} (use --force to overwrite, or 'config set' to change a value)")
    values = defaults()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    prompts = [
        ("api_key_env", args.api_key_env),
        ("api_key_file", args.api_key_file),
        ("output_dir", args.output_dir),
        ("default_model", args.default_model),
    ]
    for key, given in prompts:
        if given is not None:
            values[key] = coerce(key, given)
        elif interactive:
            current = values[key]
            shown = "" if current is None else str(current)
            answer = input(f"{key} ({SCHEMA[key].help}) [{shown}]: ").strip()
            if answer:
                values[key] = coerce(key, answer)
    if values["api_key_file"] is not None:
        key_path = Path(values["api_key_file"]).expanduser()
        if not key_path.is_file():
            raise CliError(f"api_key_file does not exist: {key_path}")
    config = Config(path, values)
    config.save()
    print(f"wrote {path}")
    print(f"override the location with ${CONFIG_ENV} or --config")


def run_upgrade(args: Any) -> None:
    path = config_path(args.config)
    if not path.is_file():
        raise CliError(f"no config file at {path}; run 'elevenlabs-cli config init'")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CliError("config file must contain a JSON object")
    unknown = [key for key in raw if key not in SCHEMA]
    if unknown:
        raise CliError(f"unknown keys in {path}: {', '.join(unknown)}; remove them first")
    added = [key for key in SCHEMA if key not in raw]
    fresh = defaults()
    for key in added:
        raw[key] = fresh[key]
    config = Config(path, validate(raw))
    config.save()
    print(f"added {len(added)} key(s): {', '.join(added) if added else 'none'}")


def run_show(args: Any) -> None:
    config = Config.load(args.config)
    print(json.dumps(config.values, indent=2, sort_keys=True))


def split_alias_key(key: str) -> tuple[str, str] | None:
    parts = key.split(".")
    if parts[0] != "voices" or len(parts) == 1:
        return None
    if len(parts) != 3 or not all(parts):
        raise CliError("alias keys look like voices.<language>.<alias>")
    return parts[1], parts[2]


def run_get(args: Any) -> None:
    config = Config.load(args.config)
    alias = split_alias_key(args.key)
    if alias is None:
        value = config.get(args.key)
    else:
        language, name = alias
        try:
            value = config.get("voices")[language][name]
        except KeyError as exc:
            raise CliError(f"no alias '{name}' for language '{language}'") from exc
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, sort_keys=True))
    elif value is None:
        print("null")
    else:
        print(value)


def run_set(args: Any) -> None:
    config = Config.load(args.config)
    alias = split_alias_key(args.key)
    if alias is None:
        if args.key == "voices":
            try:
                parsed = json.loads(args.value)
            except json.JSONDecodeError as exc:
                raise CliError("'voices' must be set as JSON, or one alias at a time with voices.<language>.<alias>") from exc
            config.set("voices", parsed)
        else:
            config.set(args.key, args.value)
    else:
        language, name = alias
        voices = config.get("voices")
        if args.value == "null":
            if language not in voices or name not in voices[language]:
                raise CliError(f"no alias '{name}' for language '{language}'")
            del voices[language][name]
            if not voices[language]:
                del voices[language]
        else:
            voices.setdefault(language, {})[name] = args.value
        config.set("voices", voices)
    config.save()
    print(f"{args.key} = {args.value}")
