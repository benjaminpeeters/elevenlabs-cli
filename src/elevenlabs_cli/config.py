"""The JSON configuration file.

Location: ``~/.config/elevenlabs-cli/config.json``, overridden by the
``ELEVENLABS_CLI_CONFIG`` environment variable or ``--config``. Nothing about a
machine lives in the code; everything user-specific lives here.

The API key never enters the file: ``api_key_env`` names an environment
variable and ``api_key_file`` optionally names a file (a bare key or an
``.env`` style ``KEY=value`` file) that is read at run time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CliError

CONFIG_ENV = "ELEVENLABS_CLI_CONFIG"
DEFAULT_PATH = Path.home() / ".config" / "elevenlabs-cli" / "config.json"


@dataclass(frozen=True)
class Field:
    """A validated top-level configuration key."""

    kind: str  # "str" | "int" | "float" | "bool" | "optional_path" | "optional_str" | "optional_float" | "voices"
    default: Any
    help: str


SCHEMA: dict[str, Field] = {
    "api_key_env": Field("str", "ELEVENLABS_API_KEY", "environment variable holding the API key"),
    "api_key_file": Field(
        "optional_str", None, "file holding the key (bare key, or KEY=value lines); read when the env var is unset"
    ),
    "output_dir": Field("optional_path", None, "folder for relative --out paths (null = the current directory)"),
    "default_model": Field("str", "eleven_v3", "model used when --model is absent"),
    "default_format": Field("str", "mp3_44100_128", "API output format when --format is absent"),
    "default_lufs": Field("optional_float", None, "loudness target for join (null = no normalisation)"),
    "confirm_above_chars": Field("int", 2000, "generations above this many characters need --yes (0 = always)"),
    "trim_threshold_db": Field("float", -40.0, "silence threshold used by trim and verify"),
    "trim_margin_ms": Field("int", 50, "audio kept on each side of the voiced content when trimming"),
    "verify_tolerance_ms": Field("int", 10, "allowed gap deviation in verify"),
    "voices": Field("voices", {}, "named voices per language: {\"en\": {\"narrator\": \"<voice id>\"}}"),
}


def config_path(override: str | None = None) -> Path:
    """The config file path: ``--config``, then the env var, then the default."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env).expanduser()
    return DEFAULT_PATH


def defaults() -> dict[str, Any]:
    return {key: (dict(field.default) if isinstance(field.default, dict) else field.default) for key, field in SCHEMA.items()}


def coerce(key: str, value: Any) -> Any:
    """Validate ``value`` for ``key`` and return it in its canonical type.

    Accepts strings (from the command line) as well as JSON-typed values.
    Raises ``CliError`` with the expectation on any mismatch.
    """
    if key not in SCHEMA:
        raise CliError(f"unknown config key '{key}' (known: {', '.join(SCHEMA)})")
    kind = SCHEMA[key].kind
    if kind == "str":
        if not isinstance(value, str) or not value:
            raise CliError(f"config '{key}' must be a non-empty string")
        return value
    if kind == "optional_str":
        if value is None or value == "" or value == "null":
            return None
        if not isinstance(value, str):
            raise CliError(f"config '{key}' must be a string or null")
        return value
    if kind == "optional_path":
        if value is None or value == "" or value == "null":
            return None
        if not isinstance(value, str):
            raise CliError(f"config '{key}' must be a path or null")
        return value
    if kind == "int":
        if isinstance(value, bool):
            raise CliError(f"config '{key}' must be an integer")
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                raise CliError(f"config '{key}' must be an integer, got '{value}'") from exc
        if not isinstance(value, int) or value < 0:
            raise CliError(f"config '{key}' must be a non-negative integer")
        return value
    if kind == "float":
        if isinstance(value, bool):
            raise CliError(f"config '{key}' must be a number")
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError as exc:
                raise CliError(f"config '{key}' must be a number, got '{value}'") from exc
        if not isinstance(value, (int, float)):
            raise CliError(f"config '{key}' must be a number")
        return float(value)
    if kind == "optional_float":
        if value is None or value == "" or value == "null":
            return None
        return coerce_float(key, value)
    if kind == "bool":
        if isinstance(value, str):
            if value.lower() in ("true", "yes", "on", "1"):
                return True
            if value.lower() in ("false", "no", "off", "0"):
                return False
            raise CliError(f"config '{key}' must be true or false, got '{value}'")
        if not isinstance(value, bool):
            raise CliError(f"config '{key}' must be true or false")
        return value
    if kind == "voices":
        if not isinstance(value, dict):
            raise CliError("config 'voices' must be an object: {\"<language>\": {\"<alias>\": \"<voice id>\"}}")
        for language, aliases in value.items():
            if not isinstance(language, str) or not language or not isinstance(aliases, dict):
                raise CliError(f"config 'voices.{language}' must be an object of alias -> voice id")
            for alias, voice_id in aliases.items():
                if not isinstance(alias, str) or not alias or not isinstance(voice_id, str) or not voice_id:
                    raise CliError(f"config 'voices.{language}.{alias}' must be a non-empty voice id string")
        return value
    raise AssertionError(f"unhandled kind {kind}")


def coerce_float(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise CliError(f"config '{key}' must be a number or null")
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise CliError(f"config '{key}' must be a number or null, got '{value}'") from exc
    if not isinstance(value, (int, float)):
        raise CliError(f"config '{key}' must be a number or null")
    return float(value)


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a complete, typed config; unknown or ill-typed keys raise."""
    if not isinstance(raw, dict):
        raise CliError("config file must contain a JSON object")
    for key in raw:
        if key not in SCHEMA:
            raise CliError(f"unknown config key '{key}' (known: {', '.join(SCHEMA)})")
    missing = [key for key in SCHEMA if key not in raw]
    if missing:
        raise CliError(f"config is missing keys: {', '.join(missing)}. Run 'elevenlabs-cli config init' or set them with 'config set'.")
    return {key: coerce(key, raw[key]) for key in SCHEMA}


class Config:
    """A loaded configuration file."""

    def __init__(self, path: Path, values: dict[str, Any]) -> None:
        self.path = path
        self.values = values

    @classmethod
    def load(cls, override: str | None = None) -> "Config":
        path = config_path(override)
        if not path.is_file():
            raise CliError(
                f"no config file at {path}. Run 'elevenlabs-cli config init' (see --help), "
                f"or point {CONFIG_ENV} / --config at an existing file."
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CliError(f"config file {path} is not valid JSON: {exc}") from exc
        return cls(path, validate(raw))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            raise CliError(f"cannot write to {self.path.parent}: {exc.strerror}; make that folder writable and retry") from exc

    def get(self, key: str) -> Any:
        if key not in SCHEMA:
            raise CliError(f"unknown config key '{key}' (known: {', '.join(SCHEMA)})")
        return self.values[key]

    def set(self, key: str, value: Any) -> None:
        self.values[key] = coerce(key, value)

    @property
    def output_dir(self) -> Path | None:
        """Base for relative output paths, or None for the current directory."""
        value = self.values["output_dir"]
        return None if value is None else Path(value).expanduser()

    def api_key(self) -> str:
        """Resolve the key from the environment, then the key file. Never logged."""
        env_name = self.values["api_key_env"]
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
        key_file = self.values["api_key_file"]
        if key_file is None:
            raise CliError(f"API key not found: set ${env_name} or configure 'api_key_file' (elevenlabs-cli config set api_key_file <path>)")
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise CliError(f"API key file not found: {path} (config 'api_key_file')")
        return read_key_file(path, env_name)

    def resolve_alias(self, name: str, language: str | None) -> str | None:
        """A configured voice alias -> voice id, or None when no alias matches.

        With ``language`` only that language's aliases are consulted; without
        it, an alias defined under several languages is ambiguous and raises.
        """
        voices: dict[str, dict[str, str]] = self.values["voices"]
        if language is not None:
            aliases = voices.get(language, {})
            return aliases.get(name)
        hits = [(lang, aliases[name]) for lang, aliases in voices.items() if name in aliases]
        if not hits:
            return None
        if len(hits) > 1:
            langs = ", ".join(lang for lang, _ in hits)
            raise CliError(f"voice alias '{name}' exists for several languages ({langs}); pass --language")
        return hits[0][1]


def read_key_file(path: Path, env_name: str) -> str:
    """A bare key on the first non-empty line, or a ``NAME=value`` line named ``env_name``."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines:
        raise CliError(f"API key file {path} is empty")
    if any("=" in line for line in lines):
        for line in lines:
            name, sep, value = line.partition("=")
            if sep and name.strip() in (env_name, "export " + env_name):
                value = value.strip().strip("'\"")
                if not value:
                    raise CliError(f"API key file {path}: '{env_name}=' is empty")
                return value
        raise CliError(f"API key file {path} has no '{env_name}=' line")
    if len(lines) != 1:
        raise CliError(f"API key file {path}: expected a single bare key or NAME=value lines")
    return lines[0]
