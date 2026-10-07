import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from elevenlabs_cli import audio as audio_mod
from elevenlabs_cli import client as api
from elevenlabs_cli.config import defaults


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A complete config in tmp_path, selected through the environment variable."""
    values = defaults()
    values["output_dir"] = str(tmp_path / "out")
    values["api_key_file"] = str(tmp_path / "key.txt")
    values["denoise"] = False  # command tests do not load a model; tests/test_denoise.py covers it
    (tmp_path / "key.txt").write_text("sk_test_not_a_real_key\n")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values))
    monkeypatch.setenv("ELEVENLABS_CLI_CONFIG", str(path))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return path


def set_config(path: Path, **updates: object) -> None:
    values = json.loads(path.read_text())
    values.update(updates)
    path.write_text(json.dumps(values))


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")


MP3_BYTES = b"RIFFfake-wav-bytes"


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The SDK boundary replaced: an account with three voices, speech that returns fixed bytes."""
    calls: dict[str, Any] = {"tts": []}
    monkeypatch.setattr(api, "make_client", lambda key: object())
    monkeypatch.setattr(api, "my_voices", lambda client, search=None: [
        {"voice_id": "id_amy", "name": "Amy", "category": "premade", "labels": {"gender": "female"}},
        {"voice_id": "id_dup1", "name": "Dup", "category": "cloned", "labels": {}},
        {"voice_id": "id_dup2", "name": "Dup", "category": "cloned", "labels": {}},
    ])

    def tts(client: Any, voice_id: str, text: str, model_id: str, output_format: str, language: Any, settings: Any, seed: Any, prev_ids: Any, prev_text: Any, next_text: Any) -> api.TtsResult:
        calls["tts"].append({"voice_id": voice_id, "text": text, "model": model_id, "seed": seed, "prev_ids": prev_ids, "language": language, "format": output_format})
        return api.TtsResult(MP3_BYTES, f"req{len(calls['tts'])}")

    monkeypatch.setattr(api, "text_to_speech", tts)

    def tts_timed(client: Any, voice_id: str, text: str, model_id: str, output_format: str, language: Any, settings: Any, seed: Any, prev_text: Any, next_text: Any) -> api.TimedResult:
        calls["tts"].append({"voice_id": voice_id, "text": text, "model": model_id, "seed": seed, "prev_ids": None, "language": language, "format": output_format, "timed": True})
        n = len(text)
        return api.TimedResult(MP3_BYTES, api.Alignment(list(text), [i * 0.05 for i in range(n)], [(i + 1) * 0.05 for i in range(n)]), [])

    monkeypatch.setattr(api, "text_to_speech_timed", tts_timed)
    monkeypatch.setattr(audio_mod, "cut_line_by_alignment", lambda src, out, *a, **k: out.write_bytes(src.read_bytes()) or (0.0, 1.0))
    monkeypatch.setattr(audio_mod, "check_not_truncated", lambda path, db, label, allow: audio_mod.TailLevel(-3.0, -20.0, -40.0, -50.0))
    monkeypatch.setattr(api, "subscription", lambda client: {
        "tier": "creator", "status": "active", "character_count": 1200, "character_limit": 100000,
        "next_character_count_reset_unix": 1800000000, "voice_slots_used": 3, "voice_limit": 30,
        "can_use_instant_voice_cloning": True, "can_use_professional_voice_cloning": False,
    })
    return calls
