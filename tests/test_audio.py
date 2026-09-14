"""One real ffmpeg round trip: tone clips with padding, trim, join, verify."""

import json
import subprocess
from pathlib import Path

import pytest

from elevenlabs_cli import audio
from elevenlabs_cli.cli import main
from elevenlabs_cli.errors import CliError
from tests.conftest import needs_ffmpeg


def tone(path: Path, seconds: float, lead: float, tail: float) -> None:
    """A 440 Hz tone of ``seconds`` with digital silence before and after."""
    filter_graph = (
        f"anullsrc=r=44100:cl=mono:d={lead}[a];sine=f=440:r=44100:d={seconds}[b];"
        f"anullsrc=r=44100:cl=mono:d={tail}[c];[a][b][c]concat=n=3:v=0:a=1"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", filter_graph, "-c:a", "pcm_s16le", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_trim_keeps_margins(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    tone(src, 1.0, 0.4, 0.6)
    result = audio.trim(src, tmp_path / "trim.wav", -40.0, 50)
    assert abs(result.voiced_start - 0.4) < 0.01
    assert abs(result.voiced_end - 1.4) < 0.01
    assert abs(result.kept_leading - 0.05) < 0.005
    assert abs(result.kept_trailing - 0.05) < 0.005
    assert abs(result.duration - 1.1) < 0.01


@needs_ffmpeg
def test_trim_without_padding(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    tone(src, 0.5, 0.0, 0.0)
    result = audio.trim(src, tmp_path / "trim.wav", -40.0, 50)
    assert result.kept_leading == 0.0
    assert abs(result.duration - 0.5) < 0.01


@needs_ffmpeg
def test_trim_silent_file_fails(tmp_path: Path) -> None:
    src = tmp_path / "silent.wav"
    audio.silence(src, 1.0, 44100, 1)
    with pytest.raises(CliError, match="no audio above"):
        audio.trim(src, tmp_path / "trim.wav", -40.0, 50)


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".wav", ".m4a", ".mp3"])
def test_join_gaps_are_exact(tmp_path: Path, ext: str) -> None:
    a, b, c = (tmp_path / f"{n}.wav" for n in "abc")
    tone(a, 0.8, 0.3, 0.2)
    tone(b, 0.6, 0.1, 0.5)
    tone(c, 0.7, 0.25, 0.25)
    items = audio.parse_spec_args([str(a), "silence:3", str(b), "silence:5", str(c)])
    out = tmp_path / f"out{ext}"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, do_trim=True, lufs=None)
    assert [g.expected for g in timing.gaps] == [3.0, 5.0]
    checks = audio.verify_gaps(out, [3.0, 5.0], -40.0, 10)
    assert all(c.ok for c in checks), checks
    # voiced 0.8 + 0.6 + 0.7, gaps 3 + 5 (margins inside), outer margins 0.05 + 0.05
    assert abs(timing.duration - 10.2) < 0.05


@needs_ffmpeg
def test_join_with_margins_larger_than_silence_fails(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.3, 0.3)
    items = audio.parse_spec_args([str(a), "silence:0.05", str(a)])
    with pytest.raises(CliError, match="shorter than the quiet margins"):
        audio.join(items, tmp_path / "out.wav", tmp_path / "work", -40.0, 50, do_trim=True, lufs=None)


@needs_ffmpeg
def test_verify_reports_mismatch(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.1, 0.1)
    items = audio.parse_spec_args([str(a), "silence:2", str(a)])
    out = tmp_path / "out.wav"
    audio.join(items, out, tmp_path / "work", -40.0, 50, do_trim=True, lufs=None)
    checks = audio.verify_gaps(out, [2.5], -40.0, 10)
    assert not checks[0].ok
    assert abs(checks[0].measured - 2.0) < 0.01
    checks = audio.verify_gaps(out, [2.0, 1.0], -40.0, 10)
    assert checks[0].ok and checks[1].measured is None


@needs_ffmpeg
def test_cli_join_and_verify(config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.2, 0.2)
    spec = tmp_path / "spec.txt"
    spec.write_text("file a.wav\nsilence 1.5\nfile a.wav\n")
    assert main(["join", "--spec", str(spec), "--out", "joined.m4a"]) == 0
    out = capsys.readouterr()
    joined = tmp_path / "out" / "joined.m4a"
    assert f"saved: {joined}" in out.out
    sidecar = tmp_path / "out" / "joined.timing.json"
    assert json.loads(sidecar.read_text())["gaps"][0]["expected"] == 1.5
    assert main(["verify", str(joined), str(sidecar)]) == 0
    assert main(["verify", str(joined), str(spec)]) == 0
    spec.write_text("file a.wav\nsilence 1.0\nfile a.wav\n")
    assert main(["verify", str(joined), str(spec)]) == 1
    assert "MISMATCH" in capsys.readouterr().out


@needs_ffmpeg
def test_decode_pcm_to_wav(tmp_path: Path) -> None:
    raw = (b"\x00\x00" * 22050)
    out = tmp_path / "x.wav"
    audio.decode_api_audio(raw, "pcm_22050", out, tmp_path / "work")
    assert abs(audio.probe(out).duration - 1.0) < 0.01


@needs_ffmpeg
def test_join_mp3_sources_with_short_quiet_tails(tmp_path: Path) -> None:
    """MP3 headers over-report the duration and TTS clips end in quiet tails shorter than 50 ms."""
    a_wav, b_wav = tmp_path / "a.wav", tmp_path / "b.wav"
    tone(a_wav, 1.0, 0.0, 0.03)
    tone(b_wav, 1.0, 0.02, 0.28)
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    audio.encode(a_wav, a, None)
    audio.encode(b_wav, b, None)
    items = audio.parse_spec_args([str(a), "silence:3", str(b), "silence:5", str(a)])
    out = tmp_path / "out.m4a"
    audio.join(items, out, tmp_path / "work", -40.0, 50, do_trim=True, lufs=None)
    checks = audio.verify_gaps(out, [3.0, 5.0], -40.0, 10)
    assert all(c.ok for c in checks), checks
