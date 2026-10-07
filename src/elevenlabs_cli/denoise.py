"""DPDFNet speech denoising, applied to every speech render unless switched off.

Renders carry a room tone that per-clip loudness normalisation can make audible. In blind
listening tests on such renders DPDFNet at 48 kHz cleaned the background with no audible voice
artefact and was preferred over every DeepFilterNet 3 setting, RNNoise, noisereduce and the
ffmpeg filters (plugin/skills/generate/references/pipeline-findings.md, "Room tone and denoising").

The inference path is a port of the offline path of DPDFNet (Copyright Ceva Inc., Apache License
2.0, https://github.com/ceva-ip/DPDFNet): a Vorbis-window STFT with half overlap, the streaming
ONNX model run frame by frame with its recurrent state, and the inverse STFT. librosa's STFT is
replaced by the equivalent numpy code below, so the only runtime needs are numpy and onnxruntime.
The ONNX models are downloaded on first use from a pinned revision of the Hugging Face repository
and checked against their SHA-256.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio, net
from .errors import CliError

MODEL_RATE = 48000
REPO = "Ceva-IP/DPDFNet"
REVISION = "dd6818d00f50c836fed43a6243ebe49116de5964"  # pinned: a moved upstream file must not change our output silently
URL = "https://huggingface.co/{repo}/resolve/{revision}/onnx/{name}.onnx"

# Model name -> SHA-256 of its ONNX file. Only the full-band models: the 16 kHz and 8 kHz ones
# would drop everything above 8 or 4 kHz.
MODELS: dict[str, str] = {
    # 2 dual-path blocks, 2.4 GMACs; about 4x faster than real time on a desktop CPU (the default)
    "dpdfnet2_48khz_hr": "7f0575a5cec0ba4ffd8f8bd657e06d007e4ccdd955d76faab922b9d3291dc14b",
    # 8 dual-path blocks, 7.2 GMACs; about real time on a desktop CPU
    "dpdfnet8_48khz_hr": "7b3afbb260a08fe9af3d16e3bda992971be1e7e951d1dee7c2d235f5c43f5631",
}
ALIASES = {"2": "dpdfnet2_48khz_hr", "8": "dpdfnet8_48khz_hr"}


def resolve_model(value: str) -> str:
    """A model name or its short alias (2, 8) to the canonical name; anything else is an error."""
    name = ALIASES.get(value, value)
    if name not in MODELS:
        known = ", ".join(f"{alias} = {full}" for alias, full in ALIASES.items())
        raise CliError(f"unknown denoise model '{value}' (known: {known})")
    return name


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "elevenlabs-cli" / "models"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def model_path(name: str, cache_dir: Path) -> Path:
    """The verified model file, downloaded into ``cache_dir`` on first use."""
    expected = MODELS[name]
    path = cache_dir / f"{name}.onnx"
    if path.is_file():
        found = _sha256(path)
        if found != expected:
            raise CliError(f"{path} does not match the pinned model (SHA-256 {found[:12]}..., expected {expected[:12]}...); delete it to download it again")
        return path
    unwritable = f"set model_cache_dir in the config to a writable folder, or make {cache_dir} writable (in an agent session: /add-dir)"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # a unique staging name, created before any network call: concurrent first runs never share a
        # file, and a cache that exists but is read-only fails here with the right remedy
        handle, staged = tempfile.mkstemp(dir=cache_dir, prefix=f".{name}.", suffix=".part")
        os.close(handle)
    except OSError as exc:
        raise CliError(f"cannot write the model cache {cache_dir}: {exc.strerror}; {unwritable}") from exc
    partial = Path(staged)
    url = URL.format(repo=REPO, revision=REVISION, name=name)
    try:
        try:
            net.download(url, partial)
        except net.FetchError as exc:
            raise CliError(f"{exc}. Check the connection, save that file by hand into {cache_dir} as {path.name}, or render with --no-denoise") from exc
        except net.WriteError as exc:
            raise CliError(f"{exc}; {unwritable}") from exc
        found = _sha256(partial)
        if found != expected:
            raise CliError(f"the downloaded {name} does not match the pinned model (SHA-256 {found[:12]}..., expected {expected[:12]}...); nothing was kept")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)  # every exit but the rename leaves no staging file (after it, nothing is there)
    return path


# --- STFT (librosa-equivalent: centred frames, reflect padding, window-sum normalised inverse) ---

def vorbis_window(length: int) -> np.ndarray:
    half = length / 2
    s = np.sin(0.5 * np.pi * (np.arange(length) + 0.5) / half)
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


def _half_overlap(window: np.ndarray, hop: int) -> int:
    n = len(window)
    if n != 2 * hop:
        raise CliError(f"internal: the STFT here is half-overlapped only (window {n}, hop {hop})")
    return n


def stft(x: np.ndarray, window: np.ndarray, hop: int) -> np.ndarray:
    """Frames x bins complex spectrum of ``x``, frames centred on multiples of ``hop``."""
    n = _half_overlap(window, hop)
    padded = np.pad(x.astype(np.float32), n // 2, mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, n)[::hop]  # a view: no copy of the signal per frame
    return np.fft.rfft(frames * window, axis=1).astype(np.complex64)


def istft(spec: np.ndarray, window: np.ndarray, hop: int) -> np.ndarray:
    """Inverse of ``stft``: overlap-add, divided by the summed squared window, centre padding removed.

    With half overlap every hop-long segment of the output is the first half of one frame plus the
    second half of the previous one, so the overlap-add is two shifted, vectorised additions.
    """
    n = _half_overlap(window, hop)
    frames = np.fft.irfft(spec, n=n, axis=1).astype(np.float32) * window
    count = len(spec)
    out = np.zeros((count + 1, hop), dtype=np.float64)
    out[:count] += frames[:, :hop]
    out[1:] += frames[:, hop:]
    square = window.astype(np.float64) ** 2
    norm = np.zeros((count + 1, hop), dtype=np.float64)
    norm[:count] += square[:hop]
    norm[1:] += square[hop:]
    out, norm = out.reshape(-1), norm.reshape(-1)
    nonzero = norm > np.finfo(np.float32).tiny
    out[nonzero] /= norm[nonzero]
    return out[n // 2: len(out) - n // 2].astype(np.float32)


# --- the model ----------------------------------------------------------------------------

class Denoiser:
    """One ONNX session, reused for every render of a command."""

    def __init__(self, path: Path) -> None:
        # onnxruntime 1.30 persists a telemetry device id and, where its cache is not writable (a sandbox),
        # writes ':memory:.ses' into the current directory and warns; the variable must be set before import
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        import onnxruntime as ort  # imported here: commands that never denoise do not pay for it

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1  # one frame at a time: more threads only add overhead
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        except Exception as exc:  # onnxruntime raises its own exception types
            raise CliError(f"onnxruntime could not load {path}: {exc}") from exc
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 2 or len(outputs) != 2:
            raise CliError(f"{path} is not a streaming DPDFNet model (expected 2 inputs and 2 outputs)")
        self.in_spec, self.in_state = inputs[0].name, inputs[1].name
        self.out_spec, self.out_state = outputs[0].name, outputs[1].name
        bins = inputs[0].shape[-2]
        if not isinstance(bins, int) or bins < 2:
            raise CliError(f"{path} does not declare its frequency bins")
        self.window = vorbis_window((bins - 1) * 2)
        self.hop = len(self.window) // 2
        self.init_state = self._initial_state()

    def _initial_state(self) -> np.ndarray:
        meta = self.session.get_modelmeta().custom_metadata_map
        try:
            size = int(meta["state_size"])
            erb_size = int(meta["erb_norm_state_size"])
            spec_size = int(meta["spec_norm_state_size"])
            erb_init = np.array([float(v) for v in meta["erb_norm_init"].split(",")], dtype=np.float32)
            spec_init = np.array([float(v) for v in meta["spec_norm_init"].split(",")], dtype=np.float32)
        except KeyError as exc:
            raise CliError(f"the denoise model lacks state metadata {exc}") from exc
        state = np.zeros(size, dtype=np.float32)
        state[:erb_size] = erb_init
        state[erb_size:erb_size + spec_size] = spec_init
        return state

    def __call__(self, x: np.ndarray) -> np.ndarray:
        """Denoise one channel at 48 kHz; the result has the length of the input."""
        if x.ndim != 1 or len(x) == 0:
            raise CliError("the denoiser takes one non-empty channel")
        delay = 2 * len(self.window)  # the model's output lags its input by two windows
        # padded by the full delay plus one hop (the inverse STFT returns whole hops only), so the
        # delayed output still reaches the last input sample; the model is causal, so trailing
        # silence changes nothing before it
        spec = stft(np.pad(x, (0, delay + self.hop)), self.window, self.hop)
        spec_ri = np.stack([spec.real, spec.imag], axis=-1)[None]  # (1, frames, bins, 2), float32 from the complex64 spectrum
        state = self.init_state.copy()
        frames = []
        for t in range(spec_ri.shape[1]):
            frame = np.ascontiguousarray(spec_ri[:, t:t + 1])  # one STFT frame: (1, 1, bins, 2)
            feed = {self.in_spec: frame, self.in_state: state}
            out, state = self.session.run([self.out_spec, self.out_state], feed)
            frames.append(out)
        enhanced = np.concatenate(frames, axis=1)
        y = istft(enhanced[0, ..., 0] + 1j * enhanced[0, ..., 1], self.window, self.hop)
        aligned = y[delay:delay + len(x)]
        if len(aligned) != len(x):
            raise CliError(f"internal: the denoiser returned {len(aligned)} samples for {len(x)}")
        return aligned


def load(name: str, cache_dir: Path) -> Callable[[np.ndarray], np.ndarray]:
    return Denoiser(model_path(name, cache_dir))


def denoise_file(path: Path, enhancer: Callable[[np.ndarray], np.ndarray], workdir: Path, sample_rate: int) -> None:
    """Denoise the WAV ``path`` in place, keeping its channel layout, at ``sample_rate``.

    The model works at 48 kHz on one channel: the file is decoded at 48 kHz, each channel is
    denoised alone, and the result is written at ``sample_rate`` (the working rate, so a render
    requested at another rate is resampled once, here). The WAV comes back 24-bit, like every
    working WAV, so the denoised signal is not requantised to 16 bits.
    """
    if path.suffix.lower() != ".wav":
        raise CliError(f"internal: denoise_file takes a WAV, got {path}")
    info = audio.probe(path)
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / f"{path.stem}.denoise-in.f32"
    audio.ffmpeg("-i", str(path), "-f", "f32le", "-ar", str(MODEL_RATE), "-ac", str(info.channels), str(raw))
    samples = np.fromfile(raw, dtype=np.float32).reshape(-1, info.channels)
    cleaned = np.stack([np.asarray(enhancer(samples[:, c]), dtype=np.float32) for c in range(info.channels)], axis=1)
    done = workdir / f"{path.stem}.denoise-out.f32"
    cleaned.tofile(done)
    staged = workdir / f"{path.stem}.denoised.wav"
    audio.ffmpeg("-f", "f32le", "-ar", str(MODEL_RATE), "-ac", str(info.channels), "-i", str(done), "-ar", str(sample_rate), *audio.INTERMEDIATE, str(staged))
    shutil.move(str(staged), str(path))  # the work folder may sit on another filesystem
    raw.unlink()
    done.unlink()
