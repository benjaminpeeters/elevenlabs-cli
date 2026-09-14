"""Commands with the SDK boundary replaced: spend guard, voice resolution, outputs."""

from pathlib import Path
from typing import Any

import pytest

from elevenlabs_cli import client as api
from elevenlabs_cli.cli import main
from tests.conftest import set_config

MP3_BYTES = b"ID3fake-mp3-bytes"


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"tts": []}
    monkeypatch.setattr(api, "make_client", lambda key: object())
    monkeypatch.setattr(api, "my_voices", lambda client, search=None: [
        {"voice_id": "id_amy", "name": "Amy", "category": "premade", "labels": {"gender": "female"}},
        {"voice_id": "id_dup1", "name": "Dup", "category": "cloned", "labels": {}},
        {"voice_id": "id_dup2", "name": "Dup", "category": "cloned", "labels": {}},
    ])

    def tts(client: Any, voice_id: str, text: str, model_id: str, output_format: str, language: Any, settings: Any, seed: Any, prev_ids: Any, prev_text: Any, next_text: Any) -> api.TtsResult:
        calls["tts"].append({"voice_id": voice_id, "text": text, "model": model_id, "seed": seed, "prev_ids": prev_ids, "language": language})
        return api.TtsResult(MP3_BYTES, f"req{len(calls['tts'])}")

    monkeypatch.setattr(api, "text_to_speech", tts)
    monkeypatch.setattr(api, "subscription", lambda client: {
        "tier": "creator", "status": "active", "character_count": 1200, "character_limit": 100000,
        "next_character_count_reset_unix": 1800000000, "voice_slots_used": 3, "voice_limit": 30,
        "can_use_instant_voice_cloning": True, "can_use_professional_voice_cloning": False,
    })
    return calls


def test_tts_writes_mp3_and_reports_path(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hello there.", "--voice", "Amy", "--seed", "7", "--out", "hello.mp3"]) == 0
    out = capsys.readouterr()
    target = tmp_path / "out" / "hello.mp3"
    assert target.read_bytes() == MP3_BYTES
    assert out.out.strip() == f"saved: {target}"
    assert "12 characters, about 12 credits" in out.err
    assert fake_api["tts"][0]["voice_id"] == "id_amy"
    assert fake_api["tts"][0]["seed"] == 7


def test_tts_uses_config_alias_per_language(config_file: Path, fake_api: dict[str, Any]) -> None:
    set_config(config_file, voices={"en": {"narrator": "id_en"}, "fr": {"narrator": "id_fr"}})
    assert main(["tts", "--text", "Bonjour.", "--voice", "narrator", "--language", "fr", "--out", "x.mp3"]) == 0
    assert fake_api["tts"][0]["voice_id"] == "id_fr"
    assert fake_api["tts"][0]["language"] == "fr"


def test_tts_refuses_above_threshold_without_tty(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, confirm_above_chars=5)
    assert main(["tts", "--text", "This is longer than five characters.", "--voice", "Amy", "--out", "x.mp3"]) == 1
    err = capsys.readouterr().err
    assert "not confirmed" in err and "--yes" in err
    assert fake_api["tts"] == []
    assert main(["--yes", "tts", "--text", "This is longer than five characters.", "--voice", "Amy", "--out", "x.mp3"]) == 0
    assert len(fake_api["tts"]) == 1


def test_tts_never_guesses_a_voice(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hi.", "--voice", "Nobody", "--out", "x.mp3"]) == 1
    assert "no voice alias, name or id matches 'Nobody'" in capsys.readouterr().err
    assert main(["tts", "--text", "Hi.", "--voice", "Dup", "--out", "x.mp3"]) == 1
    assert "several voices are named 'Dup'" in capsys.readouterr().err
    assert fake_api["tts"] == []


def test_tts_requires_out(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hi.", "--voice", "Amy"]) == 1
    assert "--out is required" in capsys.readouterr().err


def test_tts_dry_run_spends_nothing(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hello [pause] world.", "--voice", "Amy", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "characters: 13" in out and "chunks: 1" in out
    assert fake_api["tts"] == []


def test_tts_stitches_chunks_for_v2(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from elevenlabs_cli import audio

    joined: list[Any] = []
    monkeypatch.setattr(audio, "decode_api_audio", lambda data, fmt, out, work: out.write_bytes(data))
    monkeypatch.setattr(audio, "join", lambda items, out, *a, **k: joined.append(items) or out.write_bytes(b"joined"))
    text = " ".join(["This is a sentence."] * 700)  # ~14000 chars, over the 10000 limit
    assert main(["--yes", "tts", "--text", text, "--voice", "Amy", "--model", "eleven_multilingual_v2", "--out", "long.mp3"]) == 0
    assert len(fake_api["tts"]) == 2
    assert fake_api["tts"][0]["prev_ids"] == []
    assert fake_api["tts"][1]["prev_ids"] == ["req1"]
    assert len(joined[0]) == 2


def test_account_text_and_json(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["account"]) == 0
    out = capsys.readouterr().out
    assert "credits_remaining" in out and "98800" in out
    assert main(["--json", "account"]) == 0
    assert '"tier": "creator"' in capsys.readouterr().out


def test_missing_key_file_is_loud(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, api_key_file=str(tmp_path / "gone.txt"))
    assert main(["account"]) == 1
    assert "API key file not found" in capsys.readouterr().err


def test_dialogue_needs_every_speaker(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "s.txt"
    script.write_text("Ann: Hello.\nBob: Hi Ann.\n")
    assert main(["dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--out", "d.mp3"]) == 1
    assert "no --speaker mapping for: Bob" in capsys.readouterr().err
    seen: list[Any] = []
    monkeypatch.setattr(api, "text_to_dialogue", lambda client, lines, *a: seen.append(lines) or MP3_BYTES)
    assert main(["dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--speaker", "Bob=id_dup1", "--out", "d.mp3"]) == 0
    assert seen[0] == [("id_amy", "Hello."), ("id_dup1", "Hi Ann.")]
    assert main(["dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--speaker", "Bob=Amy", "--model", "eleven_multilingual_v2", "--out", "d.mp3"]) == 1
    assert "v3-class model" in capsys.readouterr().err


def test_per_call_billing_asks_unless_never(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(api, "sound_effect", lambda *a: MP3_BYTES)
    assert main(["sfx", "--text", "rain", "--out", "rain.mp3"]) == 1
    assert "not confirmed" in capsys.readouterr().err
    set_config(config_file, confirm_above_chars=100000)
    assert main(["sfx", "--text", "rain", "--out", "rain.mp3"]) == 0


def test_relative_out_uses_cwd_without_output_dir(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, output_dir=None)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["tts", "--text", "Hi.", "--voice", "Amy", "--out", "sub/hi.mp3"]) == 0
    assert (work / "sub" / "hi.mp3").read_bytes() == MP3_BYTES
    assert capsys.readouterr().out.strip() == f"saved: {(work / 'sub' / 'hi.mp3').resolve()}"


def test_unwritable_out_is_loud(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hi.", "--voice", "Amy", "--out", "/proc/nope/hi.mp3"]) == 1
    assert "cannot write to /proc/nope" in capsys.readouterr().err
