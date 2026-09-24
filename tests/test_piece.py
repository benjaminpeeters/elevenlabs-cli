"""``piece``: one take, cut at the alignment, re-timed to the script's pauses."""

import json
from pathlib import Path
from typing import Any

import pytest

from elevenlabs_cli import audio as audio_mod
from elevenlabs_cli import script
from elevenlabs_cli.cli import main
from elevenlabs_cli.cost import model_info
from elevenlabs_cli.errors import CliError
from tests.test_commands import fake_api  # noqa: F401  (fixture)

SCRIPT = """# a timed script
[pause 1.5]
Please take your time to get comfortable.
[pause 2.5]
You might take a moment to grab a blanket,
[pause 0.5]
[pause 0.6]
or a pillow.
[pause 3]
"""


def test_parse_timed_script_lines_pauses_and_merges() -> None:
    items = script.parse_timed_script(SCRIPT)
    kinds = [type(i).__name__ for i in items]
    assert kinds == ["Pause", "Line", "Pause", "Line", "Pause", "Line", "Pause"]
    assert items[0].seconds == 1.5 and items[4].seconds == 1.1  # two consecutive pauses merge
    assert items[1].text == "Please take your time to get comfortable." and items[1].number == 3
    assert script.lines_of(items) == [items[1], items[3], items[5]]


def test_parse_timed_script_is_strict() -> None:
    with pytest.raises(CliError, match="line 2: expected"):
        script.parse_timed_script("Hello.\n[pause two]\nWorld.\n")
    with pytest.raises(CliError, match="no text line"):
        script.parse_timed_script("[pause 1]\n# nothing\n")
    with pytest.raises(CliError, match="line 2.*positive"):
        script.parse_timed_script("Hello.\n[pause 0]\nWorld.\n")


def test_request_text_per_model() -> None:
    items = script.parse_timed_script("[pause 1]\nFirst line.\n[pause 4]\nSecond line.\nThird line.\n[pause 2]\n")
    v2 = script.request_text(items, model_info("eleven_multilingual_v2"))
    assert v2 == 'First line. <break time="3.0s"/> Second line. Third line.'  # breaks capped at 3 s; no break without a pause
    v3 = script.request_text(items, model_info("eleven_v3"))
    assert v3 == "First line.\n\nSecond line.\n\nThird line. " + script.SACRIFICIAL_TAIL


def test_locate_spans_with_or_without_tags_in_the_alignment() -> None:
    lines = ["First line.", "Second line."]
    with_tags = 'First line. <break time="3.0s"/> Second line.'
    assert script.locate_spans(with_tags, lines) == [(0, 10), (33, 44)]
    stripped = "First line.  Second line."
    assert script.locate_spans(stripped, lines) == [(0, 10), (13, 24)]
    with pytest.raises(CliError, match="line 2 .*not found in the alignment"):
        script.locate_spans("First line. Something else.", lines)


def test_piece_dry_run_spends_nothing(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "s.txt"
    src.write_text(SCRIPT)
    assert main(["piece", "--file", str(src), "--voice", "Amy", "--model", "eleven_multilingual_v2", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "lines: 3" in out and "pauses: 4 (8.1 s)" in out and "characters: 99" in out and "requests: 1" in out
    assert fake_api["tts"] == []


def test_piece_refuses_a_script_over_the_request_limit(config_file: Path, fake_api: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "s.txt"
    src.write_text("\n".join(["This is one line of a very long script, repeated."] * 120) + "\n")  # about 6000 chars
    assert main(["piece", "--file", str(src), "--voice", "Amy", "--model", "eleven_v3", "--dry-run"]) == 1
    assert "above the 5000" in capsys.readouterr().err


def test_piece_renders_one_take_and_retimes(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "s.txt"
    src.write_text(SCRIPT)
    monkeypatch.setattr(audio_mod, "write_api_audio", lambda data, fmt, out, work, ch: out.write_bytes(data))
    monkeypatch.setattr(audio_mod, "decode_to_wav", lambda s, t, rate, ch, lufs=None: t.write_bytes(s.read_bytes()) or audio_mod.Probe(30.0, rate, ch))
    cut_calls: list[Any] = []

    def fake_cut(source: Path, spans: Any, starts: Any, ends: Any, threshold: float, rate: int, channels: int, workdir: Path, **kw: Any) -> list[Path]:
        cut_calls.append(spans)
        workdir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(len(spans)):
            p = workdir / f"{i + 1:03d}.wav"
            p.write_bytes(b"clip")
            paths.append(p)
        return paths

    monkeypatch.setattr(audio_mod, "cut_lines_by_alignment", fake_cut)
    joined: list[Any] = []

    def fake_join(items: Any, out: Path, *a: Any, **k: Any) -> Any:
        joined.append(items)
        out.write_bytes(b"x")
        return audio_mod.Timing(str(out), -40.0, 48000, 1, [], [], [], 0.0, 12.0)

    monkeypatch.setattr(audio_mod, "join", fake_join)
    assert main(["--yes", "piece", "--file", str(src), "--voice", "Amy", "--model", "eleven_multilingual_v2", "--seed", "7", "--lufs", "-25", "--out", "p.m4a"]) == 0
    calls = fake_api["tts"]
    assert len(calls) == 1 and calls[0]["timed"] and calls[0]["model"] == "eleven_multilingual_v2" and calls[0]["seed"] == 7
    assert '<break time="2.5s"/>' in calls[0]["text"] and calls[0]["language"] is None
    assert cut_calls[0] == [(0, 40), (63, 104), (127, 138)]  # spans in the alignment (the fake returns the text itself)
    items = joined[0]
    assert [i.silence for i in items] == [1.5, None, 2.5, None, 1.1, None, 3.0]  # leading, between, trailing
    assert items[1].label == "1 Please take your time to get comfortable."
    sidecar = json.loads((tmp_path / "out" / "p.timing.json").read_text())
    assert sidecar["model"] == "eleven_multilingual_v2" and sidecar["seed"] == 7 and sidecar["take_lufs"] == -25.0
    assert f"saved: {tmp_path / 'out' / 'p.m4a'}" in capsys.readouterr().out
    # v3: paragraphs plus the sacrificial tail, cut away because it is not a line
    assert main(["--yes", "piece", "--file", str(src), "--voice", "Amy", "--model", "eleven_v3", "--out", "q.m4a"]) == 0
    assert fake_api["tts"][-1]["text"].endswith(script.SACRIFICIAL_TAIL) and len(cut_calls[-1]) == 3
