"""DPDFNet denoising: the numpy STFT, model resolution and download, the file round trip, the wiring."""

from __future__ import annotations

import hashlib
import http.client
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest

from elevenlabs_cli import audio, denoise, net
from elevenlabs_cli import client as api
from elevenlabs_cli.cli import main
from elevenlabs_cli.config import defaults
from elevenlabs_cli.errors import CliError

from tests.conftest import needs_ffmpeg, set_config


# --- STFT -------------------------------------------------------------------------------

def test_stft_istft_round_trip() -> None:
    rng = np.random.default_rng(1)
    x = rng.standard_normal(48000).astype(np.float32) * 0.1
    window = denoise.vorbis_window(960)
    spec = denoise.stft(x, window, 480)
    assert spec.shape == (1 + len(x) // 480, 481)
    y = denoise.istft(spec, window, 480)
    assert len(y) == len(x)
    assert np.max(np.abs(y - x)) < 1e-5


def test_vorbis_window_is_power_complementary() -> None:
    # the property that makes the half-overlapped analysis and synthesis reconstruct exactly
    w = denoise.vorbis_window(960)
    assert np.allclose(w[:480] ** 2 + w[480:] ** 2, 1.0, atol=1e-6)


# --- model names, cache, download ---------------------------------------------------------

def test_model_aliases() -> None:
    assert denoise.resolve_model("2") == "dpdfnet2_48khz_hr"
    assert denoise.resolve_model("8") == "dpdfnet8_48khz_hr"
    assert denoise.resolve_model("dpdfnet8_48khz_hr") == "dpdfnet8_48khz_hr"
    with pytest.raises(CliError, match="dpdfnet2_48khz_hr"):
        denoise.resolve_model("dpdfnet4")


def test_cached_model_with_wrong_hash_is_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "dpdfnet2_48khz_hr.onnx").write_bytes(b"not the model")
    monkeypatch.setattr(net, "download", lambda url, target: pytest.fail("must not download over a cached file"))
    with pytest.raises(CliError, match="SHA-256"):
        denoise.model_path("dpdfnet2_48khz_hr", tmp_path)


def test_download_is_verified_and_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"model bytes"
    monkeypatch.setitem(denoise.MODELS, "dpdfnet2_48khz_hr", hashlib.sha256(payload).hexdigest())
    seen: list[str] = []
    monkeypatch.setattr(net, "download", lambda url, target: seen.append(url) or target.write_bytes(payload))
    path = denoise.model_path("dpdfnet2_48khz_hr", tmp_path / "cache")
    assert path.read_bytes() == payload
    assert seen and denoise.REVISION in seen[0] and seen[0].endswith("onnx/dpdfnet2_48khz_hr.onnx")
    assert [p.name for p in (tmp_path / "cache").iterdir()] == ["dpdfnet2_48khz_hr.onnx"]  # no staging file left
    # a second call uses the cache
    monkeypatch.setattr(net, "download", lambda url, target: pytest.fail("downloaded twice"))
    assert denoise.model_path("dpdfnet2_48khz_hr", tmp_path / "cache") == path


def test_corrupt_download_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "download", lambda url, target: target.write_bytes(b"truncated"))
    with pytest.raises(CliError, match="SHA-256"):
        denoise.model_path("dpdfnet2_48khz_hr", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_failed_fetch_points_to_the_connection_and_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(url: str, target: Path) -> None:
        raise net.FetchError(f"could not download {url}: offline")

    monkeypatch.setattr(net, "download", offline)
    with pytest.raises(CliError, match="Check the connection"):
        denoise.model_path("dpdfnet2_48khz_hr", tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into read-only folders")
def test_read_only_cache_points_to_model_cache_dir_before_any_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o500)
    monkeypatch.setattr(net, "download", lambda url, target: pytest.fail("must not reach the network"))
    try:
        with pytest.raises(CliError, match="model_cache_dir"):
            denoise.model_path("dpdfnet2_48khz_hr", cache)
    finally:
        cache.chmod(0o700)


class FakeResponse:
    """What urlopen returns, with the body read by ``read``."""

    def __init__(self, read: Callable[[], bytes]) -> None:
        self.read = read

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_net_download_reports_fetch_and_write_failures_apart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(url: str, timeout: float) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    with pytest.raises(net.FetchError, match="connection refused"):
        net.download("https://example.invalid/x", tmp_path / "x")
    assert not (tmp_path / "x").exists()

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: FakeResponse(lambda: b"data"))
    with pytest.raises(net.WriteError, match="cannot write"):
        net.download("https://example.invalid/x", tmp_path / "missing-folder" / "x")


def test_a_body_cut_short_is_a_fetch_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def cut_short() -> bytes:
        raise http.client.IncompleteRead(b"partial", 10_000_000)

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: FakeResponse(cut_short))
    with pytest.raises(net.FetchError, match="IncompleteRead"):
        net.download("https://example.invalid/x", tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_no_staging_file_survives_an_unexpected_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(url: str, target: Path) -> None:
        target.write_bytes(b"half")
        raise KeyboardInterrupt

    monkeypatch.setattr(net, "download", crash)
    with pytest.raises(KeyboardInterrupt):
        denoise.model_path("dpdfnet2_48khz_hr", tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- file round trip (any enhancer) -------------------------------------------------------

@needs_ffmpeg
@pytest.mark.parametrize("rate,channels,working", [(48000, 1, 48000), (24000, 1, 48000), (48000, 2, 48000), (48000, 1, 44100)])
def test_denoise_file_writes_the_working_rate_and_keeps_layout_and_length(tmp_path: Path, rate: int, channels: int, working: int) -> None:
    clip = tmp_path / "clip.wav"
    audio.ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration=1", "-ac", str(channels), *audio.INTERMEDIATE, str(clip))
    before = audio.probe(clip)
    peak_before = audio.peak_dbfs(clip)
    calls: list[int] = []

    def halve(x: np.ndarray) -> np.ndarray:
        calls.append(len(x))
        return x * 0.5

    denoise.denoise_file(clip, halve, tmp_path / "work", working)
    after = audio.probe(clip)
    assert (after.sample_rate, after.channels) == (working, before.channels)
    assert abs(after.duration - before.duration) < 0.002
    assert calls == [48000] * channels  # the model always sees 48 kHz, one channel at a time
    assert audio.peak_dbfs(clip) == pytest.approx(peak_before - 20 * np.log10(2), abs=0.3)


def test_denoise_file_takes_only_a_wav(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="takes a WAV"):
        denoise.denoise_file(tmp_path / "clip.mp3", lambda x: x, tmp_path / "work", 48000)


# --- wiring into the commands ------------------------------------------------------------

@pytest.fixture
def fake_denoiser(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Path]]:
    """Replace model loading and the file pass; record (model, file) per denoised render."""
    seen: list[tuple[str, Path]] = []
    loaded: list[str] = []

    def fake_load(name: str, cache_dir: Path) -> Any:
        loaded.append(name)
        return lambda x: x

    monkeypatch.setattr(denoise, "load", fake_load)
    monkeypatch.setattr(denoise, "denoise_file", lambda path, enhancer, workdir, rate: seen.append((loaded[-1], path)))
    def write_api_audio(data: bytes, fmt: str, out: Path, work: Path, channels: int) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)  # as the real one does
        out.write_bytes(data)

    monkeypatch.setattr(audio, "write_api_audio", write_api_audio)
    monkeypatch.setattr(audio, "deliver", lambda source, out, rate, channels: out.write_bytes(source.read_bytes()))
    return seen


def run_tts(tmp_path: Path, *extra: str) -> int:
    return main(["--yes", "tts", "--text", "Rest here, there is nothing to do now.", "--voice", "Amy", "--out", str(tmp_path / "o.wav"), *extra])


def test_denoising_is_on_by_default() -> None:
    assert defaults()["denoise"] is True and defaults()["denoise_model"] == "dpdfnet2_48khz_hr"


def test_tts_denoises_by_default(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path) -> None:
    set_config(config_file, denoise=True)
    assert run_tts(tmp_path) == 0
    assert len(fake_denoiser) == 1 and fake_denoiser[0][0] == "dpdfnet2_48khz_hr"


def test_no_denoise_flag_skips(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path) -> None:
    set_config(config_file, denoise=True)
    assert run_tts(tmp_path, "--no-denoise") == 0
    assert fake_denoiser == []


def test_denoise_model_flag(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path) -> None:
    set_config(config_file, denoise=True)
    assert run_tts(tmp_path, "--denoise-model", "8") == 0
    assert fake_denoiser[0][0] == "dpdfnet8_48khz_hr"


def test_config_can_turn_it_off(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path) -> None:
    set_config(config_file, denoise=False)
    assert run_tts(tmp_path) == 0
    assert fake_denoiser == []


def test_unknown_denoise_model_is_loud(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, denoise=True)
    assert run_tts(tmp_path, "--denoise-model", "dpdfnet4") != 0
    assert "dpdfnet2_48khz_hr" in capsys.readouterr().err


def test_wav_output_is_denoised_as_a_working_wav_and_copied(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path) -> None:
    set_config(config_file, denoise=True)
    assert run_tts(tmp_path) == 0
    (_, denoised), = fake_denoiser
    assert denoised.name.endswith(".render.wav") and denoised.parent != tmp_path
    assert (tmp_path / "o.wav").is_file()


def test_the_model_loads_before_anything_is_billed(config_file: Path, fake_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, denoise=True)

    def offline(name: str, cache_dir: Path) -> Any:
        raise CliError("could not download the model")

    monkeypatch.setattr(denoise, "load", offline)
    monkeypatch.setattr(api, "text_to_speech",lambda *a, **k: pytest.fail("billed before the model was ready"))
    assert run_tts(tmp_path) != 0
    assert "could not download the model" in capsys.readouterr().err


def test_dry_run_reports_denoising_without_loading(config_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    set_config(config_file, denoise=True)
    monkeypatch.setattr(denoise, "load", lambda name, cache_dir: pytest.fail("a dry run must not load the model"))
    assert run_tts(tmp_path, "--dry-run", "--denoise-model", "8") == 0
    assert "denoise: dpdfnet8_48khz_hr" in capsys.readouterr().out
    assert run_tts(tmp_path, "--dry-run", "--no-denoise") == 0
    assert "denoise: off" in capsys.readouterr().out
    assert run_tts(tmp_path, "--dry-run", "--denoise-model", "dpdfnet4") != 0


def test_lossy_output_is_denoised_as_wav_and_encoded_once(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], tmp_path: Path) -> None:
    set_config(config_file, denoise=True)
    out_dir = tmp_path / "samples"
    assert main(["--yes", "voices", "sample", "Amy", "--out-dir", str(out_dir)]) == 0
    (_, denoised), = fake_denoiser
    assert denoised.suffix == ".wav" and denoised.parent != out_dir
    assert (out_dir / "id_amy.mp3").is_file()


def test_sound_effects_are_never_denoised(config_file: Path, fake_api: dict[str, Any], fake_denoiser: list[tuple[str, Path]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_config(config_file, denoise=True)
    monkeypatch.setattr(api, "sound_effect", lambda client, text, duration, influence, fmt, loop: b"\x00\x00" * 4800)
    assert main(["--yes", "sfx", "--text", "rain", "--out", str(tmp_path / "rain.wav")]) == 0
    assert fake_denoiser == []


# --- the real model (only where the model files are available) ----------------------------

MODEL_DIR = os.environ.get("ELEVENLABS_CLI_TEST_MODELS")


@pytest.mark.skipif(not MODEL_DIR, reason="set ELEVENLABS_CLI_TEST_MODELS to a folder holding the DPDFNet .onnx files")
def test_real_model_lowers_noise_and_keeps_length(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    enhance = denoise.load("dpdfnet2_48khz_hr", Path(MODEL_DIR))
    rng = np.random.default_rng(3)
    t = np.arange(48000 * 2) / 48000
    voiced = (np.sin(2 * np.pi * 220 * t) * (t > 0.5) * (t < 1.5) * 0.3).astype(np.float32)
    noise = (rng.standard_normal(len(t)) * 0.01).astype(np.float32)
    y = enhance(voiced + noise)
    assert len(y) == len(t)
    quiet = slice(0, int(0.4 * 48000))
    assert np.sqrt(np.mean(y[quiet] ** 2)) < 0.5 * np.sqrt(np.mean(noise[quiet] ** 2))
    assert list(tmp_path.iterdir()) == []  # no onnxruntime telemetry file left in the working directory


@pytest.mark.skipif(not MODEL_DIR, reason="set ELEVENLABS_CLI_TEST_MODELS to a folder holding the DPDFNet .onnx files")
@pytest.mark.parametrize("seconds", [1.0, 1.0123])  # a whole number of hops and not
def test_real_model_keeps_the_last_samples(seconds: float) -> None:
    """A render cut while still sounding keeps its tail: the truncation guard reads the last 30 ms.

    The model is not asked to keep a synthetic signal loud (it treats much of it as noise); the
    test is that the last 20 ms carry at least half the level of the 20 ms before them, where a lost
    tail is silence.
    """
    enhance = denoise.load("dpdfnet2_48khz_hr", Path(MODEL_DIR))
    t = np.arange(int(48000 * seconds)) / 48000
    voice = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 12)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    x = (voice / np.abs(voice).max() * 0.5 + np.random.default_rng(5).standard_normal(len(t)) * 0.005).astype(np.float32)
    y = enhance(x)
    assert len(y) == len(x)

    def rms(a: np.ndarray) -> float:
        return float(np.sqrt(np.mean(a ** 2)))

    assert rms(y[-960:]) > 0.5 * rms(y[-1920:-960])
