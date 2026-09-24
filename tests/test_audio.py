"""Real ffmpeg round trips on generated tones: trim, join, verify, noise, mix, loudness."""

import json
import subprocess
from pathlib import Path

import pytest

from elevenlabs_cli import audio
from elevenlabs_cli.cli import main
from elevenlabs_cli.errors import CliError
from tests.conftest import needs_ffmpeg

RATE = 48000


def tone(path: Path, seconds: float, lead: float, tail: float, rate: int = RATE, channels: int = 1, gain_db: float = 0.0) -> None:
    """A 440 Hz tone of ``seconds`` with digital silence before and after (ffmpeg's sine sits at -18 dBFS; gain_db raises it)."""
    layout = "mono" if channels == 1 else "stereo"
    filter_graph = (
        f"anullsrc=r={rate}:cl={layout}:d={lead}[a];sine=f=440:r={rate}:d={seconds},volume={gain_db}dB,aformat=channel_layouts={layout}[b];"
        f"anullsrc=r={rate}:cl={layout}:d={tail}[c];[a][b][c]concat=n=3:v=0:a=1"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", filter_graph, "-c:a", "pcm_s16le", str(path)],
        check=True,
    )


def gaps_ok(out: Path, timing: audio.Timing) -> list[audio.GapCheck]:
    return audio.verify_gaps(out, [g.expected for g in timing.gaps], -40.0, 10, [(g.start, g.end) for g in timing.gaps])


@needs_ffmpeg
def test_trim_keeps_margins(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    tone(src, 1.0, 0.4, 0.6)
    result = audio.trim(src, tmp_path / "trim.wav", -40.0, 50, RATE, 1)
    assert abs(result.voiced_start - 0.4) < 0.01
    assert abs(result.voiced_end - 1.4) < 0.01
    assert abs(result.kept_leading - 0.05) < 0.005
    assert abs(result.kept_trailing - 0.05) < 0.005
    assert abs(result.duration - 1.1) < 0.01
    assert audio.probe(tmp_path / "trim.wav").sample_rate == RATE


@needs_ffmpeg
def test_trim_without_padding(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    tone(src, 0.5, 0.0, 0.0)
    result = audio.trim(src, tmp_path / "trim.wav", -40.0, 50, RATE, 1)
    assert result.kept_leading == 0.0
    assert abs(result.duration - 0.5) < 0.01


@needs_ffmpeg
def test_trim_silent_file_fails(tmp_path: Path) -> None:
    src = tmp_path / "silent.wav"
    audio.silence(src, 1.0, RATE, 1)
    with pytest.raises(CliError, match="no audio above"):
        audio.trim(src, tmp_path / "trim.wav", -40.0, 50, RATE, 1)


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".wav", ".m4a", ".mp3", ".flac"])
def test_join_gaps_are_exact(tmp_path: Path, ext: str) -> None:
    a, b, c = (tmp_path / f"{n}.wav" for n in "abc")
    tone(a, 0.8, 0.3, 0.2)
    tone(b, 0.6, 0.1, 0.5)
    tone(c, 0.7, 0.25, 0.25)
    items = audio.parse_spec_args([str(a), "silence:3", str(b), "silence:5", str(c)])
    out = tmp_path / f"out{ext}"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    assert [g.expected for g in timing.gaps] == [3.0, 5.0]
    assert timing.sample_rate == RATE and timing.channels == 1
    checks = gaps_ok(out, timing)
    assert all(c.ok for c in checks), checks
    # voiced 0.8 + 0.6 + 0.7, gaps 3 + 5 (margins inside), outer margins 0.05 + 0.05
    assert abs(timing.duration - 10.2) < 0.05


@needs_ffmpeg
def test_join_converts_mixed_rates_and_widens_to_stereo(tmp_path: Path) -> None:
    """A 44.1 kHz mono clip, a 192 kHz stereo bed and a 48 kHz clip end up on one timeline."""
    a = tmp_path / "a441.wav"
    tone(a, 0.5, 0.1, 0.1, rate=44100)
    bed = tmp_path / "bed192.wav"
    tone(bed, 1.0, 0.0, 0.0, rate=192000, channels=2)
    c = tmp_path / "c48.wav"
    tone(c, 0.5, 0.1, 0.1)
    items = audio.parse_spec_args([str(a), "silence:2", str(bed), "silence:1.5", str(c)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    info = audio.probe(out)
    assert info.sample_rate == RATE and info.channels == 2
    assert abs(timing.duration - (0.55 + 2 + 1.0 + 1.5 + 0.55)) < 0.05
    assert all(c.ok for c in gaps_ok(out, timing))
    forced = audio.join(items, tmp_path / "mono.wav", tmp_path / "work2", -40.0, 50, True, None, RATE, 1)
    assert forced.channels == 1 and audio.probe(tmp_path / "mono.wav").channels == 1


@needs_ffmpeg
def test_join_per_clip_loudness(tmp_path: Path) -> None:
    loud = tmp_path / "loud.wav"
    quiet = tmp_path / "quiet.wav"
    tone(loud, 2.0, 0.1, 0.1)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(loud), "-af", "volume=-12dB", "-c:a", "pcm_s16le", str(quiet)], check=True)
    assert audio.loudness(quiet).integrated_lufs < audio.loudness(loud).integrated_lufs - 10
    items = audio.parse_spec_args([str(loud), "silence:1", str(quiet)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, -18.0, RATE, None)
    assert audio.probe(out).sample_rate == RATE  # loudnorm did not leak 192 kHz
    assert all(c.ok for c in gaps_ok(out, timing))
    first = audio.loudness(tmp_path / "work" / "000_clip.wav").integrated_lufs
    second = audio.loudness(tmp_path / "work" / "002_clip.wav").integrated_lufs
    assert abs(first - second) < 2.0


@needs_ffmpeg
def test_join_tight_gap_shrinks_margins_and_stays_exact(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.3, 0.3)
    items = audio.parse_spec_args([str(a), "silence:0.05", str(a)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    assert timing.clips[0].kept_trailing <= 0.025 and timing.clips[1].kept_leading <= 0.025
    assert all(c.ok for c in gaps_ok(out, timing)), gaps_ok(out, timing)


def read_samples(path: Path) -> list[float]:
    import wave, array
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1
        data = array.array("h", w.readframes(w.getnframes()))
    return [x / 32768 for x in data]


@needs_ffmpeg
def test_join_overlap_shortens_records_and_keeps_headroom(tmp_path: Path) -> None:
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    tone(a, 1.0, 0.1, 0.1, gain_db=17.0)  # near full scale: the overlap would exceed 0 dBFS without the headroom gain
    tone(b, 1.0, 0.1, 0.1, gain_db=17.0)
    items = audio.parse_spec_args([str(a), "overlap:0.3", str(b)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    assert len(timing.overlaps) == 1 and timing.overlaps[0].expected == 0.3 and not timing.gaps
    assert abs(timing.overlaps[0].end - timing.overlaps[0].start - 0.3) < 0.002
    assert abs(timing.duration - (1.1 + 1.1 - 0.3 - 0.1)) < 0.03  # two clips with margins, minus the overlap
    assert timing.headroom_gain_db < 0
    assert max(abs(x) for x in read_samples(out)) <= 0.95


@needs_ffmpeg
def test_join_overlap_longer_than_previous_speech_fails(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.4, 0.1, 0.1)
    items = audio.parse_spec_args([str(a), "overlap:0.5", str(a)])
    with pytest.raises(CliError, match="longer than the previous clip's speech"):
        audio.join(items, tmp_path / "out.wav", tmp_path / "work", -40.0, 50, True, None, RATE, None)


@needs_ffmpeg
def test_edge_fades_remove_steps_at_every_cut(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.0, 0.0)  # no margins at all: the cut lands on a full-scale sample without fades
    items = audio.parse_spec_args([str(a), "silence:0.4", str(a), "silence:0.3", str(a)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    samples = read_samples(out)
    for clip in timing.clips:
        for edge in (clip.start, clip.end):
            i = int(edge * RATE)
            window = samples[max(0, i - 40):i + 40]
            steps = [abs(x - y) for x, y in zip(window, window[1:])]
            assert max(steps) < 0.06, (edge, max(steps))  # a 440 Hz full-scale tone moves ~0.058 per sample at most
    assert all(c.ok for c in gaps_ok(out, timing))


@needs_ffmpeg
def test_spec_file_with_overlap_and_fade(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.6, 0.1, 0.1)
    bed = tmp_path / "bed.wav"
    audio.noise(bed, "pink", 1.0, RATE, 1, 0.3, 1, None, 0.0, 0.0, None)
    spec = tmp_path / "spec.txt"
    spec.write_text("file a.wav\noverlap 0.2\nfile a.wav\nsilence 1\nfile bed.wav fade=0.3\n")
    items = audio.parse_spec_lines(spec.read_text().splitlines(), tmp_path)
    assert items[1].overlap == 0.2 and items[4].fade == 0.3
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    assert len(timing.overlaps) == 1 and len(timing.gaps) == 1
    assert not timing.gaps[0].verifiable  # next to a faded bed the silence merges with the fade


@needs_ffmpeg
def test_verify_by_position_ignores_pauses_inside_speech(tmp_path: Path) -> None:
    """A clip with a 0.6 s hole inside it must not shift the gap table when positions are known."""
    holed = tmp_path / "holed.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"sine=f=440:r={RATE}:d=0.5[a];anullsrc=r={RATE}:cl=mono:d=0.6[b];sine=f=440:r={RATE}:d=0.5[c];[a][b][c]concat=n=3:v=0:a=1",
         "-c:a", "pcm_s16le", str(holed)],
        check=True,
    )
    plain = tmp_path / "plain.wav"
    tone(plain, 0.5, 0.1, 0.1)
    items = audio.parse_spec_args([str(holed), "silence:1", str(plain)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    assert all(c.ok for c in gaps_ok(out, timing))
    sequential = audio.verify_gaps(out, [1.0], -40.0, 10)
    assert not all(c.ok for c in sequential)  # the in-speech hole is counted first without positions


@needs_ffmpeg
def test_verify_reports_mismatch(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.1, 0.1)
    items = audio.parse_spec_args([str(a), "silence:2", str(a)])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    positions = [(g.start, g.end) for g in timing.gaps]
    checks = audio.verify_gaps(out, [2.5], -40.0, 10, positions)
    assert not checks[0].ok and abs(checks[0].measured - 2.0) < 0.01


@needs_ffmpeg
def test_cli_join_verify_measure(config_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.2, 0.2)
    spec = tmp_path / "spec.txt"
    spec.write_text("file a.wav\nsilence 1.5\nfile a.wav\n")
    assert main(["join", "--spec", str(spec), "--out", "joined.m4a"]) == 0
    out = capsys.readouterr()
    joined = tmp_path / "out" / "joined.m4a"
    assert f"saved: {joined}" in out.out and "48000 Hz, mono" in out.err
    sidecar = tmp_path / "out" / "joined.timing.json"
    data = json.loads(sidecar.read_text())
    assert data["gaps"][0]["expected"] == 1.5 and data["sample_rate"] == 48000
    assert main(["verify", str(joined), str(sidecar)]) == 0
    assert main(["verify", str(joined), str(spec)]) == 0
    assert "no timing sidecar" in capsys.readouterr().err
    spec.write_text("file a.wav\nsilence 1.0\nfile a.wav\n")
    assert main(["verify", str(joined), str(spec)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
    assert main(["measure", str(joined), "--timing", str(sidecar)]) == 0
    text = capsys.readouterr().out
    assert "integrated_lufs" in text and "48000" in text and "ok" in text


@needs_ffmpeg
def test_write_api_pcm_to_wav_keeps_layout(tmp_path: Path) -> None:
    raw = b"\x00\x00" * 48000 * 2  # one second of stereo 48 kHz silence, interleaved
    out = tmp_path / "x.wav"
    audio.write_api_audio(raw, "pcm_48000", out, tmp_path / "work", 2)
    info = audio.probe(out)
    assert abs(info.duration - 1.0) < 0.01 and info.sample_rate == 48000 and info.channels == 2


@needs_ffmpeg
@pytest.mark.parametrize("color,channels", [("white", 1), ("pink", 1), ("red", 2)])
def test_noise_has_exact_length_and_layout(tmp_path: Path, color: str, channels: int) -> None:
    out = tmp_path / f"{color}.wav"
    audio.noise(out, color, 2.0, RATE, channels, 0.5, 7, None, 0.0, 0.0)
    info = audio.probe(out)
    assert abs(info.duration - 2.0) < 0.005 and info.sample_rate == RATE and info.channels == channels
    assert audio.detect_silences(out, -40.0, 0.05) == []


@needs_ffmpeg
def test_noise_with_lufs_keeps_rate(tmp_path: Path) -> None:
    out = tmp_path / "n.wav"
    audio.noise(out, "brown", 3.0, RATE, 1, 0.5, 1, -20.0)
    assert audio.probe(out).sample_rate == RATE


@needs_ffmpeg
def test_noise_edges_are_soft(tmp_path: Path) -> None:
    out = tmp_path / "bed.wav"
    audio.noise(out, "red", 3.0, RATE, 1, 0.5, 5, None)
    samples = read_samples(out)
    head, tail = samples[: int(0.05 * RATE)], samples[-int(0.05 * RATE):]
    assert max(abs(x) for x in head) < 0.03 and max(abs(x) for x in tail) < 0.03  # first and last 50 ms under -30 dB
    for edge in (head, tail):
        assert max(abs(x - y) for x, y in zip(edge, edge[1:])) < 0.02  # no step at the edges
    raw = tmp_path / "raw.wav"
    audio.noise(raw, "red", 3.0, RATE, 1, 0.5, 5, None, 0.0, 0.0, 0.0)
    assert max(abs(x) for x in read_samples(raw)[: int(0.05 * RATE)]) > 0.03  # without fades the edge is live


@needs_ffmpeg
def test_noise_rejects_bad_colour(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="unknown noise colour 'green'"):
        audio.noise(tmp_path / "x.wav", "green", 1.0, RATE, 1, 0.5, None, None)


@needs_ffmpeg
def test_mix_bed_under_speech(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 1.0, 0.1, 0.1)
    items = audio.parse_spec_args([str(a), "silence:2", str(a)])
    piece = tmp_path / "piece.wav"
    audio.join(items, piece, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    bed = tmp_path / "bed.wav"
    audio.noise(bed, "brown", 1.0, RATE, 2, 0.5, 3, None, 0.1, 0.1)  # shorter than the piece: must loop
    out = tmp_path / "mixed.m4a"
    info = audio.mix(piece, [audio.Bed(str(bed), -18.0)], out, tmp_path / "mixwork", RATE, None, 0.5, 0.5, None, None)
    ref = audio.probe(piece)
    assert abs(info.duration - ref.duration) < 0.1 and info.sample_rate == RATE and info.channels == 2
    # the former 2 s gap now carries the bed: no silence of 2 s remains
    assert all(g.length < 1.5 for g in audio.detect_silences(out, -40.0, 0.05))


@needs_ffmpeg
def test_mix_with_ducking_and_two_beds(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 1.0, 0.0, 0.0)
    bed1 = tmp_path / "b1.wav"
    bed2 = tmp_path / "b2.wav"
    audio.noise(bed1, "pink", 0.5, RATE, 1, 0.3, 1, None, 0.05, 0.05)
    audio.noise(bed2, "white", 0.5, RATE, 1, 0.3, 2, None, 0.05, 0.05)
    out = tmp_path / "mixed.wav"
    info = audio.mix(a, [audio.Bed(str(bed1), -20.0), audio.Bed(str(bed2), -26.0)], out, tmp_path / "w", RATE, 1, 0.1, 0.1, 6.0, -18.0)
    assert info.channels == 1 and abs(info.duration - 1.0) < 0.05
    assert abs(audio.loudness(out).integrated_lufs - (-18.0)) < 2.0


@needs_ffmpeg
def test_mix_rejects_bad_fades(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 1.0, 0.0, 0.0)
    with pytest.raises(CliError, match="exceed the speech length"):
        audio.mix(a, [audio.Bed(str(a), -10.0)], tmp_path / "o.wav", tmp_path / "w", RATE, None, 3.0, 3.0, None, None)


def tone_cut(path: Path, seconds: float, gain_db: float = 0.0) -> None:
    """A tone that stops dead at full level (what a truncated render looks like)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=f=220:r={RATE}:d={seconds}",
         "-af", f"volume={gain_db}dB", "-c:a", "pcm_s16le", str(path)],
        check=True,
    )


def tone_decaying(path: Path, seconds: float) -> None:
    """A tone that fades out over 200 ms and then rests 100 ms in silence (what a natural render looks like)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=f=220:r={RATE}:d={seconds}",
         "-af", f"afade=t=out:st={seconds - 0.2}:d=0.2,apad=pad_dur=0.1", "-c:a", "pcm_s16le", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_tail_level_detects_cut_and_passes_decay(tmp_path: Path) -> None:
    cut, decay = tmp_path / "cut.wav", tmp_path / "decay.wav"
    tone_cut(cut, 1.0)
    tone_decaying(decay, 1.0)
    assert audio.tail_level(cut).truncated(-30.0)
    assert not audio.tail_level(decay).truncated(-30.0)
    with pytest.raises(CliError, match="ends while still loud"):
        audio.check_not_truncated(cut, -30.0, "line 1", allow=False)
    audio.check_not_truncated(cut, -30.0, "line 1", allow=True)  # warning only


@needs_ffmpeg
def test_tail_level_relative_rule_catches_quiet_cut(tmp_path: Path) -> None:
    quiet = tmp_path / "quiet.wav"
    tone_cut(quiet, 1.0, gain_db=-34.0)  # tail peak about -34 dB: under the absolute threshold
    level = audio.tail_level(quiet)
    assert level.tail_peak_db < -30.0
    assert level.truncated(-30.0)  # but within 20 dB of the clip's own peak


def test_alignment_truncation_rule() -> None:
    assert audio.alignment_truncated([0.1, 0.5, 0.95], 1.0)
    assert not audio.alignment_truncated([0.1, 0.5, 0.80], 1.0)
    assert audio.alignment_truncated([], 1.0)


def test_span_from_alignment_extends_into_the_gap_only() -> None:
    starts = [0.0, 0.2, 0.4, 1.2, 1.4]
    ends = [0.2, 0.4, 0.6, 1.4, 1.6]
    assert audio.span_from_alignment(starts, ends, 0, 2, 2.0) == (0.0, 0.85)  # +0.25 decay, before the next char at 1.2
    assert audio.span_from_alignment(starts, ends, 3, 4, 1.7) == (1.2, 1.7)  # capped by the file end
    with pytest.raises(CliError, match="out of range"):
        audio.span_from_alignment(starts, ends, 0, 9, 2.0)


@needs_ffmpeg
def test_cut_span(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    tone(src, 2.0, 0.0, 0.0)
    info = audio.cut_span(src, tmp_path / "cut.wav", 0.5, 1.25, RATE, 1)
    assert abs(info.duration - 0.75) < 0.005 and info.sample_rate == RATE


@needs_ffmpeg
def test_join_leading_and_trailing_silence(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    tone(a, 0.5, 0.2, 0.2)
    items = audio.parse_spec_args(["silence:1.5", str(a), "silence:2", str(a), "silence:0.8"])
    out = tmp_path / "out.wav"
    timing = audio.join(items, out, tmp_path / "work", -40.0, 50, True, None, RATE, None)
    assert [g.expected for g in timing.gaps] == [1.5, 2.0, 0.8]
    assert timing.gaps[0].start == 0.0 and abs(timing.gaps[0].end - 1.5) < 0.002
    assert abs(timing.duration - (1.5 + 0.5 + 2.0 + 0.5 + 0.8)) < 0.03
    assert all(c.ok for c in gaps_ok(out, timing)), gaps_ok(out, timing)
    with pytest.raises(CliError, match="overlap"):
        audio.validate_items(audio.parse_spec_args(["overlap:0.2", str(a)]))


def three_tones(path: Path, gap_between: float, trailing: float = 0.2) -> None:
    """Tone 0.8 s, a gap, tone 0.5 s, a gap, tone 0.4 s, then ``trailing`` silence."""
    g = f"anullsrc=r={RATE}:cl=mono:d={gap_between}" if gap_between > 0 else None
    parts = [f"sine=f=440:r={RATE}:d=0.8[a]"]
    chain = "[a]"
    n = 1
    for tone_d, label in ((0.5, "b"), (0.4, "c")):
        if g:
            parts.append(f"{g}[g{label}]")
            chain += f"[g{label}]"
            n += 1
        parts.append(f"sine=f=330:r={RATE}:d={tone_d}[{label}]")
        chain += f"[{label}]"
        n += 1
    parts.append(f"anullsrc=r={RATE}:cl=mono:d={trailing}[t]")
    chain += "[t]"
    n += 1
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", ";".join(parts) + f";{chain}concat=n={n}:v=0:a=1", "-c:a", "pcm_s16le", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_cut_lines_by_alignment_cuts_at_the_pauses(tmp_path: Path) -> None:
    src = tmp_path / "take.wav"
    three_tones(src, 0.3)  # tones at 0..0.8, 1.1..1.6, 1.9..2.3
    # 6 fake characters per line; alignment ends a little early, next starts a little late, as real ones do
    starts = [0.0, 0.2, 0.4, 0.6, 1.15, 1.3, 1.45, 1.5, 1.95, 2.05, 2.15, 2.2]
    ends = [0.2, 0.4, 0.6, 0.75, 1.3, 1.45, 1.5, 1.55, 2.05, 2.15, 2.2, 2.25]
    spans = [(0, 3), (4, 7), (8, 11)]
    clips = audio.cut_lines_by_alignment(src, spans, starts, ends, -40.0, RATE, 1, tmp_path / "lines")
    assert [p.name for p in clips] == ["001.wav", "002.wav", "003.wav"]
    durations = [audio.probe(p).duration for p in clips]
    assert 0.80 <= durations[0] <= 0.90 and 0.50 <= durations[1] <= 0.62 and 0.40 <= durations[2] <= 0.65
    for p in clips:
        assert not audio.tail_level(p).truncated(-30.0)
    glued = tmp_path / "glued.wav"
    three_tones(glued, 0.0)  # no pause at all between the lines
    with pytest.raises(CliError, match="no pause between line 1 and line 2"):
        audio.cut_lines_by_alignment(glued, spans, starts, ends, -40.0, RATE, 1, tmp_path / "lines2")


@needs_ffmpeg
def test_cut_line_by_alignment_ends_in_the_silence_after_the_line(tmp_path: Path) -> None:
    """Line = tone 0..0.8 s, then 0.3 s of silence, then a filler tone; the alignment claims the
    last character ends at 0.70 (early, as real alignments do) and the filler starts at 1.20 (late)."""
    src = tmp_path / "src.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"sine=f=440:r={RATE}:d=0.8[a];anullsrc=r={RATE}:cl=mono:d=0.3[b];sine=f=330:r={RATE}:d=0.5[c];[a][b][c]concat=n=3:v=0:a=1",
         "-c:a", "pcm_s16le", str(src)],
        check=True,
    )
    starts = [0.0, 0.2, 0.4, 0.6, 1.20, 1.40]
    ends = [0.2, 0.4, 0.6, 0.70, 1.40, 1.60]
    start, end = audio.cut_line_by_alignment(src, tmp_path / "line.wav", starts, ends, 0, 3, -40.0, RATE, 1)
    assert start == 0.0 and 0.80 <= end <= 0.86
    assert not audio.tail_level(tmp_path / "line.wav").truncated(-30.0)
