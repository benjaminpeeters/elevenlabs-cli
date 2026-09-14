import json
"""Commands with the SDK boundary replaced: spend guard, voice resolution, outputs."""

from pathlib import Path
from typing import Any

import pytest

from elevenlabs_cli import audio as audio_mod
from elevenlabs_cli import client as api
from elevenlabs_cli.cli import main
from tests.conftest import set_config

MP3_BYTES = b"RIFFfake-wav-bytes"


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


def test_tts_writes_lossless_clip_and_reports_path(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hello there.", "--voice", "Amy", "--seed", "7", "--out", "hello.wav"]) == 0
    out = capsys.readouterr()
    target = tmp_path / "out" / "hello.wav"
    assert target.read_bytes() == MP3_BYTES
    assert out.out.strip() == f"saved: {target}"
    assert "12 characters, about 12 credits" in out.err
    assert fake_api["tts"][0]["voice_id"] == "id_amy"
    assert fake_api["tts"][0]["seed"] == 7
    assert fake_api["tts"][0]["format"] == "wav_48000"


def test_tts_uses_config_alias_per_language(config_file: Path, fake_api: dict[str, Any]) -> None:
    set_config(config_file, voices={"en": {"narrator": "id_en"}, "fr": {"narrator": "id_fr"}})
    assert main(["tts", "--text", "Bonjour à toutes et à tous, et merci d'être venus si nombreux ce matin.", "--voice", "narrator", "--language", "fr", "--out", "x.wav"]) == 0
    assert fake_api["tts"][0]["voice_id"] == "id_fr"
    assert fake_api["tts"][0]["language"] == "fr"


def test_tts_refuses_above_threshold_without_tty(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, confirm_above_chars=5)
    assert main(["tts", "--text", "This is longer than five characters.", "--voice", "Amy", "--out", "x.wav"]) == 1
    err = capsys.readouterr().err
    assert "not confirmed" in err and "--yes" in err
    assert fake_api["tts"] == []
    assert main(["--yes", "tts", "--text", "This is longer than five characters.", "--voice", "Amy", "--out", "x.wav"]) == 0
    assert len(fake_api["tts"]) == 1


def test_tts_never_guesses_a_voice(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hi.", "--voice", "Nobody", "--out", "x.wav"]) == 1
    assert "no voice alias, name or id matches 'Nobody'" in capsys.readouterr().err
    assert main(["tts", "--text", "Hi.", "--voice", "Dup", "--out", "x.wav"]) == 1
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
    monkeypatch.setattr(audio, "write_api_audio", lambda data, fmt, out, work, ch: out.write_bytes(data))
    monkeypatch.setattr(audio, "join", lambda items, out, *a, **k: joined.append(items) or out.write_bytes(b"joined"))
    text = " ".join(["This is a sentence."] * 700)  # ~14000 chars, over the 10000 limit
    assert main(["--yes", "tts", "--text", text, "--voice", "Amy", "--model", "eleven_multilingual_v2", "--out", "long.wav"]) == 0
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
    script.write_text("Ann: Hello there, how are you doing this fine morning?\nBob: Hi Ann, all good.\n")
    assert main(["dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--out", "d.wav"]) == 1
    assert "no --speaker mapping for: Bob" in capsys.readouterr().err
    seen: list[Any] = []
    monkeypatch.setattr(api, "text_to_dialogue", lambda client, lines, *a: seen.append(lines) or MP3_BYTES)
    monkeypatch.setattr(audio_mod, "write_api_audio", lambda data, fmt, out, work, ch: out.write_bytes(data))

    def fake_join(items: Any, out: Path, *a: Any, **k: Any) -> Any:
        out.write_bytes(b"x")
        return audio_mod.Timing(str(out), -40.0, 48000, 1, [], [], [], 0.0, 3.0)

    monkeypatch.setattr(audio_mod, "join", fake_join)
    assert main(["--yes", "dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--speaker", "Bob=id_dup1", "--mode", "single", "--out", "d.wav"]) == 0
    assert seen[0] == [("id_amy", "Hello there, how are you doing this fine morning?"), ("id_dup1", "Hi Ann, all good.")]
    assert main(["dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--speaker", "Bob=Amy", "--mode", "single", "--model", "eleven_multilingual_v2", "--out", "d.wav"]) == 1
    assert "v3-class model" in capsys.readouterr().err


def test_per_call_billing_asks_unless_never(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(api, "sound_effect", lambda client, text, duration, influence, fmt, loop: seen.append(fmt) or MP3_BYTES)
    assert main(["sfx", "--text", "rain", "--out", "rain.wav"]) == 1
    assert "not confirmed" in capsys.readouterr().err
    set_config(config_file, confirm_above_chars=100000)
    monkeypatch.setattr(audio_mod, "write_api_audio", lambda data, fmt, out, work, ch: out.write_bytes(data))
    assert main(["sfx", "--text", "rain", "--out", "rain.wav"]) == 0
    assert seen == ["pcm_48000"]


def test_relative_out_uses_cwd_without_output_dir(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, output_dir=None)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert main(["tts", "--text", "Hi.", "--voice", "Amy", "--out", "sub/hi.wav"]) == 0
    assert (work / "sub" / "hi.wav").read_bytes() == MP3_BYTES
    assert capsys.readouterr().out.strip() == f"saved: {(work / 'sub' / 'hi.wav').resolve()}"


def test_unwritable_out_is_loud(config_file: Path, fake_api: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Hi.", "--voice", "Amy", "--out", "/proc/nope/hi.wav"]) == 1
    assert "cannot write to /proc/nope" in capsys.readouterr().err


def test_short_lines_switch_to_v2_unless_model_given(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tts", "--text", "Bonjour !", "--voice", "Amy", "--language", "fr", "--out", "b.wav"]) == 0
    assert fake_api["tts"][-1]["model"] == "eleven_multilingual_v2"
    assert fake_api["tts"][-1]["language"] is None  # v2 does not accept language_code
    assert "short text: rendered with eleven_multilingual_v2" in capsys.readouterr().err
    assert main(["tts", "--text", "Bonjour !", "--voice", "Amy", "--model", "eleven_v3", "--out", "b.wav"]) == 0
    assert fake_api["tts"][-1]["model"] == "eleven_v3"
    long_text = "This sentence is comfortably longer than the sixty-character short-line threshold."
    assert main(["tts", "--text", long_text, "--voice", "Amy", "--out", "c.wav"]) == 0
    assert fake_api["tts"][-1]["model"] == "eleven_v3"


def test_dialogue_per_turn_mocked(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from elevenlabs_cli import audio as A
    script = tmp_path / "s.txt"
    script.write_text("Ann: Margaret, you came! I honestly wasn't sure anyone would show up on a Tuesday morning at all.\nBob: [quick] Never in doubt.\nAnn: Good.\n")
    # dry run: plan table, no render
    assert main(["dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--speaker", "Bob=id_dup1,model=eleven_multilingual_v2,speed=0.9", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "gap 0.05 s" in out and "quick" in out and "eleven_multilingual_v2" in out and fake_api["tts"] == []
    # render: one request per line, seeds per speaker, neighbouring context, then a timeline join (mocked)
    joined: list[Any] = []
    monkeypatch.setattr(A, "write_api_audio", lambda data, fmt, out, work, ch: out.write_bytes(data))
    def fake_join(items: Any, out: Path, *a: Any, **k: Any) -> Any:
        joined.append(items)
        out.write_bytes(b"x")
        return A.Timing(str(out), -40.0, 48000, 1, [], [], [], 0.0, 3.0)

    monkeypatch.setattr(A, "join", fake_join)
    assert main(["--yes", "dialogue", "--file", str(script), "--speaker", "Ann=Amy", "--speaker", "Bob=id_dup1,model=eleven_multilingual_v2,speed=0.9", "--seed", "5", "--out", "d.wav"]) == 0
    calls = fake_api["tts"]
    assert [c["voice_id"] for c in calls] == ["id_amy", "id_dup1", "id_amy"]
    assert calls[0]["model"] == "eleven_v3" and calls[0]["timed"] and calls[0]["text"].endswith("Alright, then.")  # v3 line: sacrificial tail, cut by alignment
    assert calls[1]["model"] == "eleven_multilingual_v2" and calls[2]["model"] == "eleven_multilingual_v2"  # "Good." is short
    assert calls[0]["seed"] == calls[2]["seed"] and calls[0]["seed"] != calls[1]["seed"]
    assert calls[0]["seed"] <= 4294967295
    items = joined[0]
    assert [i.file is not None for i in items] == [True, False, True, False, True]
    assert items[1].silence == 0.05 and items[3].silence is not None
    assert items[0].label == "1 Ann"
    sidecar = json.loads((tmp_path / "out" / "d.timing.json").read_text())
    assert sidecar["mode"] == "per-turn" and sidecar["seed"] == 5


def test_dialogue_chunking_and_single_mode_model_check(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from elevenlabs_cli import turns
    from elevenlabs_cli.commands.dialogue import chunk_lines
    lines = turns.parse_script("\n".join(f"A: {'word ' * 300}end." if i % 2 == 0 else f"B: {'word ' * 300}end." for i in range(4)))
    chunks = chunk_lines(lines)
    assert len(chunks) == 4 and all(len(c) == 1 for c in chunks)  # 1500-char lines never share a 2000-char request
    script = tmp_path / "s.txt"
    script.write_text("A: Hello there my friend, how have you been these last few weeks?\nB: Fine, thanks.\n")
    assert main(["dialogue", "--file", str(script), "--speaker", "A=Amy", "--speaker", "B=id_dup1", "--mode", "single", "--model", "eleven_multilingual_v2", "--dry-run"]) == 1
    assert "v3-class model" in capsys.readouterr().err


def test_models_diff() -> None:
    from elevenlabs_cli.commands.account import diff_models
    from elevenlabs_cli.cost import ModelInfo
    table = {"eleven_v3": ModelInfo("eleven_v3", 1.0, 5000, False, True), "old_model": ModelInfo("old_model", 1.0, 100, False, False)}
    live = [{"model_id": "eleven_v3", "name": "V3", "maximum_text_length_per_request": 6000}, {"model_id": "eleven_v4", "name": "V4", "maximum_text_length_per_request": 8000}]
    rows = diff_models(live, table)
    assert ("eleven_v4", "new", "V4, max 8000 chars") in rows
    assert ("eleven_v3", "limit", "API 6000, table 5000") in rows
    assert ("old_model", "gone", "not in the live list") in rows


def test_audition_dry_run_and_rate_shortlist(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from elevenlabs_cli import lab
    root = tmp_path / "lab"
    (root / "protocols" / "en-dialogue-elderly").mkdir(parents=True)
    (root / "protocols" / "en-dialogue-elderly" / "isolation.txt").write_text("Two winters in Sanaa, back in the nineties. Long before this war.\n")
    assert main(["voices", "audition", "--use-case", "en-dialogue-elderly", "--protocol", "isolation", "--voices", "Amy", "--grid", "default", "--dry-run"]) == 0
    out = capsys.readouterr()
    assert out.out.count("Amy") == 7 and "7 trials" in out.err
    assert main(["voices", "audition", "--use-case", "en-dialogue-elderly", "--protocol", "longform", "--voices", "Amy", "--dry-run"]) == 1
    assert "no protocol en-dialogue-elderly/longform" in capsys.readouterr().err
    t = lab.Trial("2026-09-14-en-dialogue-elderly-isolation-eleven-v3-st0.35", "id_amy", "Amy", {}, "en", "en-dialogue-elderly", "isolation", "x#1", "eleven_v3", "2.68.0", {"stability": 0.35}, 7, "2026-09-14", "a.m4a")
    lab.save_trial(root, t)
    assert main(["voices", "rate", t.trial_id, "4", "--note", "warm but slow"]) == 0
    assert "4/5 warm but slow" in capsys.readouterr().out
    assert main(["voices", "shortlist", "--use-case", "en-dialogue-elderly"]) == 0
    assert "Amy" in capsys.readouterr().out
    assert main(["voices", "rate", t.trial_id, "9"]) == 1
