"""ffmpeg-backed audio operations: probe, trim, silence, noise, join, mix, measure, encode.

Pipeline rules:

- One working rate (``sample_rate`` in the config, 48 kHz by default). Every
  clip, silence and bed is converted once to it, so nothing is ever resampled
  twice and nothing is concatenated at mismatched rates.
- Lossless until delivery: intermediates are 24-bit WAV; the container of the
  final file follows the ``--out`` extension.
- Timing is exact by construction: clips are trimmed to their voiced content
  (keeping ``margin_ms`` of quiet audio on each side so onsets survive), the
  digital silence inserted between two clips is shortened by the margins they
  keep, and ``verify_gaps`` re-reads the result with the same threshold.
- ``mix`` (beds under a joined piece) runs last, because a bed above the
  silence threshold makes the gaps unmeasurable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import CliError

API_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
INTERMEDIATE = ["-c:a", "pcm_s24le"]  # 24-bit while working, so sums and gains never clip or round audibly


def delivery_args(ext: str, channels: int) -> list[str]:
    """Encoder settings per container; lossy bitrates scale with the channel count."""
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    if ext == ".flac":
        return ["-c:a", "flac"]
    if ext == ".m4a":
        return ["-c:a", "aac", "-b:a", "192k" if channels > 1 else "128k", "-movflags", "+faststart"]
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    if ext == ".opus":
        return ["-c:a", "libopus", "-b:a", "96k" if channels > 1 else "64k"]
    raise CliError(f"unsupported output extension '{ext}' (supported: .wav, .flac, .m4a, .mp3, .opus)")


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise CliError(f"{tool} not found on PATH; install ffmpeg to use trim, join, mix, verify, measure and clips")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise CliError(f"{args[0]} failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}")
    return result


def ffmpeg(*args: str) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


@dataclass(frozen=True)
class Probe:
    duration: float
    sample_rate: int
    channels: int


def probe(path: Path) -> Probe:
    require_ffmpeg()
    if not path.is_file():
        raise CliError(f"audio file not found: {path}")
    result = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels:format=duration", "-of", "json", str(path),
        ]
    )
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise CliError(f"no audio stream in {path}")
    stream = streams[0]
    duration = data.get("format", {}).get("duration")
    if duration is None:
        raise CliError(f"ffprobe reports no duration for {path}")
    return Probe(float(duration), int(stream["sample_rate"]), int(stream["channels"]))


# --- silence detection -----------------------------------------------------------

SILENCE_START = re.compile(r"silence_start: ([0-9.]+)")
SILENCE_END = re.compile(r"silence_end: ([0-9.]+)")


@dataclass(frozen=True)
class Gap:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def detect_silences(path: Path, threshold_db: float, min_length: float) -> list[Gap]:
    """Every stretch below ``threshold_db`` lasting at least ``min_length`` seconds."""
    require_ffmpeg()
    total = probe(path).duration
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", f"silencedetect=noise={threshold_db}dB:d={min_length}", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CliError(f"ffmpeg silencedetect failed on {path}:\n{result.stderr.strip()}")
    starts = [float(m.group(1)) for m in SILENCE_START.finditer(result.stderr)]
    ends = [float(m.group(1)) for m in SILENCE_END.finditer(result.stderr)]
    if len(ends) == len(starts) - 1:
        ends.append(total)  # silence runs to the end of the file
    if len(ends) != len(starts):
        raise CliError(f"silencedetect output on {path} is inconsistent ({len(starts)} starts, {len(ends)} ends)")
    return [Gap(s, e) for s, e in zip(starts, ends)]


# --- decoding, trimming, generating ---------------------------------------------------

EDGE_MIN_SILENCE = 0.01  # seconds; leading/trailing quiet this short is still cut, so a gap never inherits it


def decode_to_wav(source: Path, target: Path, sample_rate: int, channels: int, lufs: float | None = None) -> Probe:
    """Decode any container to 24-bit WAV at the working rate and layout.

    Every later timestamp is then exact (compressed containers report a header
    duration that differs from the decoded length by tens of milliseconds) and
    every clip concatenates without resampling. With ``lufs`` the clip is
    loudness-normalised on the way; the filter would otherwise output 192 kHz,
    hence the explicit rate after it.
    """
    if not source.is_file():
        raise CliError(f"audio file not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(source)]
    if lufs is not None:
        args += ["-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11"]
    ffmpeg(*args, "-ar", str(sample_rate), "-ac", str(channels), *INTERMEDIATE, str(target))
    return probe(target)


@dataclass(frozen=True)
class TrimResult:
    source: str
    output: str
    voiced_start: float  # in the source
    voiced_end: float
    kept_leading: float  # quiet audio kept before the voiced content, seconds
    kept_trailing: float
    duration: float  # of the output


def trim(source: Path, output: Path, threshold_db: float, margin_ms: int, sample_rate: int, channels: int, lufs: float | None = None) -> TrimResult:
    """Cut ``source`` to its voiced content plus up to ``margin_ms`` on each side."""
    decoded = output.with_name(output.stem + "_decoded.wav")
    info = decode_to_wav(source, decoded, sample_rate, channels, lufs)
    silences = detect_silences(decoded, threshold_db, EDGE_MIN_SILENCE)
    voiced_start = 0.0
    voiced_end = info.duration
    if silences and silences[0].start <= 0.002:
        voiced_start = silences[0].end
    if silences and silences[-1].end >= info.duration - 0.002 and silences[-1].start > voiced_start:
        voiced_end = silences[-1].start
    if voiced_end <= voiced_start:
        raise CliError(f"{source} contains no audio above {threshold_db} dB; nothing to trim to")
    margin = margin_ms / 1000.0
    start = max(0.0, voiced_start - margin)
    end = min(info.duration, voiced_end + margin)
    ffmpeg("-i", str(decoded), "-af", f"atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS", *INTERMEDIATE, str(output))
    decoded.unlink()
    return TrimResult(str(source), str(output), voiced_start, voiced_end, voiced_start - start, end - voiced_end, end - start)


def silence(output: Path, seconds: float, sample_rate: int, channels: int) -> None:
    """Write ``seconds`` of digital silence."""
    require_ffmpeg()
    if seconds <= 0:
        raise CliError(f"silence length must be positive, got {seconds}")
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={'mono' if channels == 1 else 'stereo'}", "-t", f"{seconds:.6f}", *INTERMEDIATE, str(output))


NOISE_COLORS = {"white": "white", "pink": "pink", "brown": "brown", "red": "brown", "blue": "blue", "violet": "violet"}


def noise(
    output: Path,
    color: str,
    seconds: float,
    sample_rate: int,
    channels: int,
    amplitude: float,
    seed: int | None,
    lufs: float | None,
    fade_in: float = 0.5,
    fade_out: float = 0.5,
    highpass: float | None = None,
) -> None:
    """Write ``seconds`` of coloured noise (ffmpeg anoisesrc); the container follows the extension.

    Brown (red) noise is an integrated random walk that drifts away from zero,
    so its raw edges thump; a 40 Hz high-pass (the default for brown/red)
    removes the drift and the fades soften what remains. Stereo noise uses
    two independent generators (different seeds) so it has width.
    """
    require_ffmpeg()
    if color not in NOISE_COLORS:
        raise CliError(f"unknown noise colour '{color}' (known: {', '.join(NOISE_COLORS)})")
    if seconds <= 0:
        raise CliError(f"noise length must be positive, got {seconds}")
    if not 0 < amplitude <= 1:
        raise CliError(f"amplitude must be in (0, 1], got {amplitude}")
    if channels not in (1, 2):
        raise CliError(f"channels must be 1 or 2, got {channels}")
    if fade_in < 0 or fade_out < 0 or fade_in + fade_out > seconds:
        raise CliError(f"fades ({fade_in} + {fade_out} s) must be non-negative and fit in {seconds} s")
    if highpass is None and NOISE_COLORS[color] == "brown":
        highpass = 40.0
    base = f"anoisesrc=color={NOISE_COLORS[color]}:r={sample_rate}:a={amplitude}:d={seconds:.6f}"
    seed_value = 0 if seed is None else seed
    steps: list[str] = []
    if highpass:
        steps.append(f"highpass=f={highpass:g}")
    if lufs is not None:
        steps.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
    if fade_in > 0:
        steps.append(f"afade=t=in:st=0:d={fade_in:.6f}")
    if fade_out > 0:
        steps.append(f"afade=t=out:st={seconds - fade_out:.6f}:d={fade_out:.6f}")
    filters = "".join(step + "," for step in steps)
    output.parent.mkdir(parents=True, exist_ok=True)
    _noise_impl(output, base, seed_value, channels, filters, sample_rate)


def _noise_impl(output: Path, base: str, seed_value: int, channels: int, filters: str, sample_rate: int) -> None:
    ext = output.suffix.lower()
    if channels == 1:
        ffmpeg("-f", "lavfi", "-i", f"{base}:seed={seed_value}", "-af", f"{filters}aformat=sample_rates={sample_rate}", *delivery_args(ext, 1), str(output))
        return
    graph = (
        f"{base}:seed={seed_value}[l];{base}:seed={seed_value + 1}[r];"
        f"[l][r]join=inputs=2:channel_layout=stereo,{filters}aformat=sample_rates={sample_rate}[out]"
    )
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=0.01", "-filter_complex", graph, "-map", "[out]", *delivery_args(ext, 2), str(output))


# --- join ----------------------------------------------------------------------


@dataclass(frozen=True)
class JoinItem:
    """One entry of a join spec: a file (with an optional long fade for beds), a silence, or an overlap.

    ``silence`` is the exact gap between voiced content; ``overlap`` makes the
    next voiced content start that many seconds before the previous one ends.
    """

    file: str | None = None
    silence: float | None = None
    overlap: float | None = None
    fade: float = 0.0
    label: str | None = None


@dataclass(frozen=True)
class PlacedClip:
    file: str
    start: float
    end: float
    kept_leading: float  # effective quiet margin before the voiced content (may be shrunk for a tight gap)
    kept_trailing: float
    voiced_start: float
    voiced_end: float
    label: str | None = None


@dataclass(frozen=True)
class ExpectedGap:
    start: float  # where the voiced content of the previous clip ends
    end: float  # where the voiced content of the next clip starts
    expected: float  # the silence asked for in the spec
    verifiable: bool = True  # False next to a faded bed: its quiet fade merges with the silence


@dataclass(frozen=True)
class Overlap:
    after: int  # index of the clip the next one overlaps
    start: float  # where the next voiced content starts
    end: float  # where the previous voiced content ends
    expected: float


@dataclass(frozen=True)
class Timing:
    output: str
    threshold_db: float
    sample_rate: int
    channels: int
    clips: list[PlacedClip]
    gaps: list[ExpectedGap]
    overlaps: list[Overlap]
    headroom_gain_db: float
    duration: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


EDGE_FADE_IN = 0.005  # seconds; kills clicks at cut points, moves a -40 dB silence edge by under 1 ms
EDGE_FADE_OUT = 0.008
HEADROOM_DBFS = -1.0  # a summed timeline louder than this gets a static gain at encode (a limiter would shift timing)


def parse_spec_lines(lines: list[str], base: Path) -> list[JoinItem]:
    """``file <path>`` / ``silence <seconds>`` lines; blank lines and ``#`` comments ignored."""
    items: list[JoinItem] = []
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, rest = line.partition(" ")
        rest = rest.strip()
        if keyword == "file" and rest:
            fade = 0.0
            if " fade=" in rest:
                rest, _, fade_text = rest.rpartition(" fade=")
                fade = parse_seconds(fade_text, f"line {number} fade")
            path = Path(rest.strip()).expanduser()
            items.append(JoinItem(file=str(path if path.is_absolute() else base / path), fade=fade))
        elif keyword == "silence" and rest:
            items.append(JoinItem(silence=parse_seconds(rest, f"line {number}")))
        elif keyword == "overlap" and rest:
            items.append(JoinItem(overlap=parse_seconds(rest, f"line {number}", allow_zero=True)))
        else:
            raise CliError(f"spec line {number}: expected 'file <path> [fade=<s>]', 'silence <seconds>' or 'overlap <seconds>', got '{line}'")
    return items


def parse_spec_args(args: list[str]) -> list[JoinItem]:
    """Positional items: a path, or ``silence:<seconds>``."""
    items: list[JoinItem] = []
    for arg in args:
        if arg.startswith("silence:"):
            items.append(JoinItem(silence=parse_seconds(arg[len("silence:"):], arg)))
        elif arg.startswith("overlap:"):
            items.append(JoinItem(overlap=parse_seconds(arg[len("overlap:"):], arg, allow_zero=True)))
        else:
            items.append(JoinItem(file=str(Path(arg).expanduser())))
    return items


def parse_seconds(text: str, where: str, allow_zero: bool = False) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise CliError(f"{where}: expected a number of seconds, got '{text}'") from exc
    if value < 0 or (value == 0 and not allow_zero):
        raise CliError(f"{where}: must be positive, got {value}")
    return value


def validate_items(items: list[JoinItem]) -> None:
    if not items:
        raise CliError("join spec is empty")
    if not any(item.file is not None for item in items):
        raise CliError("a join spec needs at least one file")
    if items[0].overlap is not None or items[-1].overlap is not None:
        raise CliError("a join spec cannot start or end with an overlap (a leading or trailing silence is fine)")
    for previous, current in zip(items, items[1:]):
        if previous.file is None and current.file is None:
            raise CliError("two consecutive silence/overlap entries in the join spec; merge them into one")
    for item in items:
        if item.file is not None and not Path(item.file).is_file():
            raise CliError(f"join: file not found: {item.file} (input paths are relative to the current directory; use the absolute paths printed by tts)")


def widest_layout(paths: list[Path], requested: int | None) -> int:
    """The output channel count: as requested, else stereo as soon as one input is stereo."""
    if requested is not None:
        if requested not in (1, 2):
            raise CliError(f"channels must be 1 or 2, got {requested}")
        return requested
    return max(min(probe(p).channels, 2) for p in paths)


def join(
    items: list[JoinItem],
    output: Path,
    workdir: Path,
    threshold_db: float,
    margin_ms: int,
    do_trim: bool,
    lufs: float | None,
    sample_rate: int,
    channels: int | None,
) -> Timing:
    """Place trimmed clips on a timeline with exact gaps or overlaps and sum them into ``output``.

    Every clip is decoded once to ``sample_rate`` and the output layout, so
    mixed sources never stretch the timeline. ``lufs`` normalises each clip on
    its own. A ``silence:N`` keeps N seconds between voiced content; a tight
    gap shrinks the kept quiet margins to fit. An ``overlap:N`` starts the next
    voiced content N seconds before the previous one ends (it must be shorter
    than that clip's speech). Every clip edge gets a short fade, so cuts never
    click. The sum is written in floating point, its peak measured, and a
    static gain applied at encode when it exceeds the headroom.
    """
    require_ffmpeg()
    validate_items(items)
    workdir.mkdir(parents=True, exist_ok=True)
    layout = widest_layout([Path(item.file) for item in items if item.file], channels)

    # 1. clips: trim (or decode) each file once
    clips_raw: list[tuple[int, JoinItem, TrimResult]] = []  # (item index, item, trim info)
    for index, item in enumerate(items):
        if item.file is None:
            continue
        wav = workdir / f"{index:03d}_clip.wav"
        if do_trim and item.fade == 0.0:
            info = trim(Path(item.file), wav, threshold_db, margin_ms, sample_rate, layout, lufs)
        else:
            duration = decode_to_wav(Path(item.file), wav, sample_rate, layout, lufs).duration
            info = TrimResult(item.file, str(wav), 0.0, duration, 0.0, 0.0, duration)
        clips_raw.append((index, item, info))

    # 2. the signed gap before each clip (None for the first): +N silence, -N overlap;
    #    a silence before the first clip or after the last one is a lead-in or a tail of the piece
    gaps_before: list[float | None] = [None]
    for k in range(1, len(clips_raw)):
        between = items[clips_raw[k - 1][0] + 1:clips_raw[k][0]]
        if not between:
            gaps_before.append(0.0)  # two files back to back: voiced content touches
        elif between[0].silence is not None:
            gaps_before.append(between[0].silence)
        else:
            gaps_before.append(-float(between[0].overlap or 0.0))
    head = items[:clips_raw[0][0]]
    tail = items[clips_raw[-1][0] + 1:]
    leading = head[0].silence if head else None
    trailing = tail[0].silence if tail else None

    # 3. effective margins: a tight gap shrinks the quiet margins on both sides to fit
    leads: list[float] = []
    trails: list[float] = []
    for k, (_, _, info) in enumerate(clips_raw):
        g_before = gaps_before[k] if k > 0 else leading
        g_after = gaps_before[k + 1] if k + 1 < len(clips_raw) else trailing
        share = 2 if k > 0 else 1  # an inner gap is shared with the neighbour; a lead-in belongs to this clip alone
        lead = info.kept_leading if g_before is None or g_before < 0 else min(info.kept_leading, g_before / share)
        share = 2 if k + 1 < len(clips_raw) else 1
        trail = info.kept_trailing if g_after is None or g_after < 0 else min(info.kept_trailing, g_after / share)
        leads.append(lead)
        trails.append(trail)

    # 4. placement (seconds), one signed formula
    placed: list[PlacedClip] = []
    gaps: list[ExpectedGap] = []
    overlaps: list[Overlap] = []
    offsets: list[float] = []
    for k, (index, item, info) in enumerate(clips_raw):
        cut_start = info.kept_leading - leads[k]
        cut_end = info.duration - (info.kept_trailing - trails[k])
        duration = cut_end - cut_start
        if k == 0:
            offset = 0.0 if leading is None else leading - leads[0]
        else:
            prev = placed[-1]
            g = gaps_before[k]
            assert g is not None
            if g < 0:
                speech = prev.voiced_end - prev.voiced_start
                if -g >= speech:
                    raise CliError(f"overlap of {-g:g} s is longer than the previous clip's speech ({speech:.2f} s): {prev.file}")
            offset = prev.voiced_end + g - leads[k]
        voiced_start = offset + leads[k]
        voiced_end = offset + duration - trails[k]
        clip = PlacedClip(info.source, offset, offset + duration, leads[k], trails[k], voiced_start, voiced_end, item.label)
        if k == 0 and leading is not None:
            gaps.append(ExpectedGap(0.0, voiced_start, leading, verifiable=item.fade == 0))
        if k > 0:
            g = gaps_before[k]
            assert g is not None
            prev = placed[-1]
            if g > 0:
                faded = item.fade > 0 or clips_raw[k - 1][1].fade > 0
                gaps.append(ExpectedGap(prev.voiced_end, voiced_start, g, verifiable=not faded))
            elif g < 0:
                overlaps.append(Overlap(k - 1, voiced_start, prev.voiced_end, -g))
        placed.append(clip)
        offsets.append(offset)
    total: float | None = None
    if trailing is not None:
        total = placed[-1].voiced_end + trailing
        gaps.append(ExpectedGap(placed[-1].voiced_end, total, trailing, verifiable=clips_raw[-1][1].fade == 0))

    # 5. the ffmpeg graph: per clip cut in samples, edge fades, delay in samples; one sum
    inputs: list[str] = []
    chains: list[str] = []
    for k, (index, item, info) in enumerate(clips_raw):
        inputs += ["-i", info.output]
        cut_start_s = int(round((info.kept_leading - leads[k]) * sample_rate))
        cut_end_s = int(round((info.duration - (info.kept_trailing - trails[k])) * sample_rate))
        n = cut_end_s - cut_start_s
        fade_in = int(round((item.fade or EDGE_FADE_IN) * sample_rate))
        fade_out = int(round((item.fade or EDGE_FADE_OUT) * sample_rate))
        fade_in = min(fade_in, n // 2)
        fade_out = min(fade_out, n // 2)
        delay = int(round(offsets[k] * sample_rate))
        chains.append(
            f"[{k}:a]atrim=start_sample={cut_start_s}:end_sample={cut_end_s},asetpts=PTS-STARTPTS,"
            f"afade=t=in:ss=0:ns={fade_in},afade=t=out:ss={n - fade_out}:ns={fade_out},"
            f"adelay=delays={delay}S:all=1[c{k}]"
        )
    summed_label = "[sum]" if total is not None else "[out]"
    chains.append("".join(f"[c{k}]" for k in range(len(clips_raw))) + f"amix=inputs={len(clips_raw)}:normalize=0:duration=longest{summed_label}")
    if total is not None:
        chains.append(f"[sum]apad=whole_len={int(round(total * sample_rate))}[out]")  # the trailing silence, exact in samples
    graph = workdir / "graph.txt"
    graph.write_text(";\n".join(chains) + "\n", encoding="utf-8")
    summed = workdir / "joined.wav"
    ffmpeg(*inputs, "-filter_complex_script", str(graph), "-map", "[out]", "-c:a", "pcm_f32le", str(summed))

    # 6. headroom: a static gain keeps the sidecar exact
    peak = peak_dbfs(summed)
    gain = 0.0 if peak <= HEADROOM_DBFS else HEADROOM_DBFS - peak
    encode(summed, output, sample_rate, layout, None, gain_db=gain)
    return Timing(str(output), threshold_db, sample_rate, layout, placed, gaps, overlaps, round(gain, 2), probe(output).duration)


def peak_dbfs(path: Path) -> float:
    """Peak level of a file in dBFS (astats)."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "astats=measure_perchannel=none:measure_overall=Peak_level", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CliError(f"ffmpeg astats failed on {path}:\n{result.stderr.strip()}")
    match = re.search(r"Peak level dB: (-?[0-9.]+|-inf)", result.stderr)
    if not match:
        raise CliError(f"could not read the peak level of {path}")
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


# --- delivery and API bytes --------------------------------------------------------------


def encode(source: Path, output: Path, sample_rate: int, channels: int, lufs: float | None = None, gain_db: float = 0.0) -> None:
    """Encode ``source`` into the container chosen by ``output``'s extension, at the working rate and layout."""
    require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(source)]
    filters = []
    if gain_db:
        filters.append(f"volume={gain_db:+.3f}dB")
    if lufs is not None:
        filters.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
    if filters:
        args += ["-af", ",".join(filters)]
    ffmpeg(*args, "-ar", str(sample_rate), "-ac", str(channels), *delivery_args(output.suffix.lower(), channels), str(output))


def deliver(source: Path, output: Path, sample_rate: int, channels: int) -> None:
    """Copy a working WAV to a ``.wav`` output, encode for any other container."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".wav":
        shutil.copyfile(source, output)
    else:
        encode(source, output, sample_rate, channels)


def api_family(api_format: str) -> str:
    return api_format.split("_")[0]


def api_rate(api_format: str) -> int:
    parts = api_format.split("_")
    try:
        return int(parts[1])
    except (IndexError, ValueError) as exc:
        raise CliError(f"cannot read a sample rate from output format '{api_format}'") from exc


def write_api_audio(data: bytes, api_format: str, output: Path, workdir: Path, source_channels: int) -> None:
    """Write bytes returned by the API as ``output``.

    Raw PCM (``pcm_*``) is wrapped into a WAV; ``wav_*`` and ``mp3_*`` are
    copied when the extension matches and transcoded otherwise. No resampling
    happens here: the API was asked for the working rate already.
    """
    ext = output.suffix.lower()
    family = api_family(api_format)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_ext = {"mp3": ".mp3", "opus": ".opus", "wav": ".wav"}.get(family)
    if raw_ext == ext:
        output.write_bytes(data)
        return
    require_ffmpeg()
    workdir.mkdir(parents=True, exist_ok=True)
    rate = api_rate(api_format)
    if family in ("pcm", "ulaw", "alaw"):
        codec = {"pcm": "s16le", "ulaw": "mulaw", "alaw": "alaw"}[family]
        source = workdir / f"api.{codec}"
        source.write_bytes(data)
        head = ["-f", codec, "-ar", str(rate), "-ac", str(source_channels), "-i", str(source)]
    else:
        source = workdir / f"api{raw_ext}"
        source.write_bytes(data)
        head = ["-i", str(source)]
    ffmpeg(*head, *delivery_args(ext, source_channels), str(output))


# --- verify and measure ------------------------------------------------------------------


@dataclass(frozen=True)
class GapCheck:
    index: int
    expected: float
    measured: float | None
    ok: bool


def verify_gaps(
    path: Path,
    expected: list[float],
    threshold_db: float,
    tolerance_ms: int,
    positions: list[tuple[float, float]] | None = None,
) -> list[GapCheck]:
    """Measure the silences of ``path`` against the expected gap lengths.

    With ``positions`` (the start/end of each gap from a timing sidecar) the
    silence containing each gap's midpoint is measured, so pauses inside
    speech never count as gaps. Without positions (a plain spec file) the
    internal silences are matched in order, using a minimum length of half
    the shortest expected gap; that is weaker and reported as such by verify.
    """
    if not expected:
        raise CliError("nothing to verify: the spec has no silences")
    tolerance = tolerance_ms / 1000.0
    total = probe(path).duration
    checks: list[GapCheck] = []
    if positions is not None:
        if len(positions) != len(expected):
            raise CliError(f"timing sidecar lists {len(positions)} gap positions for {len(expected)} expected gaps")
        found = detect_silences(path, threshold_db, 0.05)
        for index, (want, (start, end)) in enumerate(zip(expected, positions)):
            mid = (start + end) / 2
            hit = next((g for g in found if g.start <= mid <= g.end), None)
            measured = hit.length if hit else None
            checks.append(GapCheck(index, want, measured, measured is not None and abs(measured - want) <= tolerance))
        return checks
    shortest = min(expected)
    found = detect_silences(path, threshold_db, max(0.05, shortest / 2))
    inner = [g for g in found if g.start > 0.001 and g.end < total - 0.001]
    for index, want in enumerate(expected):
        measured = inner[index].length if index < len(inner) else None
        checks.append(GapCheck(index, want, measured, measured is not None and abs(measured - want) <= tolerance))
    for extra in inner[len(expected):]:
        checks.append(GapCheck(len(checks), 0.0, extra.length, False))
    return checks


@dataclass(frozen=True)
class Loudness:
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range: float


def loudness(path: Path) -> Loudness:
    """Integrated loudness, true peak and range (ffmpeg ebur128)."""
    require_ffmpeg()
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CliError(f"ffmpeg ebur128 failed on {path}:\n{result.stderr.strip()}")
    summary = result.stderr[result.stderr.rfind("Summary:"):]

    def grab(label: str) -> float:
        match = re.search(label + r":\s*(-?[0-9.]+|-inf)", summary)
        if not match:
            raise CliError(f"could not read {label} from ffmpeg ebur128 output for {path}")
        return float("-inf") if match.group(1) == "-inf" else float(match.group(1))

    return Loudness(grab("I"), grab("Peak"), grab("LRA"))


# --- truncation guard --------------------------------------------------------------

TAIL_WINDOW = 0.030  # seconds inspected at the end of a render
RELATIVE_TAIL_DB = 20.0  # a tail within this many dB of the clip's own peak is not a natural decay


@dataclass(frozen=True)
class TailLevel:
    clip_peak_db: float
    clip_rms_db: float
    tail_peak_db: float
    tail_rms_db: float

    def truncated(self, truncation_db: float) -> bool:
        """Loud tail by the absolute rule, or a tail within 20 dB of the clip's own peak (quiet renders)."""
        return self.tail_peak_db > min(truncation_db, self.clip_peak_db - RELATIVE_TAIL_DB)


def tail_level(path: Path, window: float = TAIL_WINDOW) -> TailLevel:
    """Peak and RMS of the whole clip and of its last ``window`` seconds, in one ffmpeg pass.

    A natural utterance decays into silence; an API render cut mid-phoneme
    ends loud. Both numbers are needed: the absolute one for normal renders,
    the relative one for quiet ones.
    """
    require_ffmpeg()
    duration = probe(path).duration
    start = max(0.0, duration - window)
    stats = "astats=measure_perchannel=none:measure_overall=Peak_level+RMS_level"
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-filter_complex",
            f"[0:a]asplit[w][t];[w]{stats}[wo];[t]atrim=start={start:.6f},{stats}[to]",
            "-map", "[wo]", "-map", "[to]", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CliError(f"ffmpeg astats failed on {path}:\n{result.stderr.strip()}")
    # The two summaries arrive in whichever order the branches finish; the filter instance
    # label tells them apart: asplit is instance 0, the whole-clip astats 1, atrim 2, the tail astats 3.
    def grab(instance: int, label: str) -> float:
        match = re.search(rf"\[Parsed_astats_{instance} @ [^\]]+\] {label} dB: (-?[0-9.]+|-inf)", result.stderr)
        if not match:
            raise CliError(f"could not read {label} of astats instance {instance} for {path}")
        return float("-inf") if match.group(1) == "-inf" else float(match.group(1))

    return TailLevel(grab(1, "Peak level"), grab(1, "RMS level"), grab(3, "Peak level"), grab(3, "RMS level"))


def check_not_truncated(path: Path, truncation_db: float, label: str, allow: bool) -> TailLevel:
    """Fail loudly when a render ends while still loud; ``allow`` downgrades it to a warning on stderr."""
    level = tail_level(path)
    if level.truncated(truncation_db):
        message = (
            f"{label} ends while still loud: last 30 ms peak {level.tail_peak_db:.1f} dB (clip peak {level.clip_peak_db:.1f} dB); "
            f"the API cut it short. Remedies: give the line context (render it inside a longer request and cut by alignment), "
            f"use --model eleven_multilingual_v2, or lengthen the text. --allow-truncated keeps it. File kept at {path}"
        )
        if not allow:
            raise CliError(message)
        print(f"warning: {message}", file=sys.stderr)
    return level


def alignment_truncated(alignment_ends: list[float], duration: float, frame: float = 0.080) -> bool:
    """True when the last character's end sits within one API frame of the audio end (no decay was rendered)."""
    if not alignment_ends:
        return True
    return duration - max(alignment_ends) < frame


def cut_span(source: Path, output: Path, start: float, end: float, sample_rate: int, channels: int) -> Probe:
    """Cut ``[start, end]`` seconds out of ``source`` into a WAV at the working rate."""
    if end <= start:
        raise CliError(f"cut span is empty: {start:.3f}..{end:.3f}")
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(source), "-af", f"atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS", "-ar", str(sample_rate), "-ac", str(channels), *INTERMEDIATE, str(output))
    return probe(output)


def span_from_alignment(starts: list[float], ends: list[float], first_char: int, last_char: int, duration: float, decay: float = 0.25) -> tuple[float, float]:
    """The time span of characters ``first_char..last_char`` (inclusive), extended by up to ``decay``
    seconds after the last character but never into the next character. Coarse: alignment times
    precede the acoustic decay by tens of milliseconds; use ``cut_line_by_alignment`` for a real cut."""
    if not 0 <= first_char <= last_char < len(ends):
        raise CliError(f"alignment has {len(ends)} characters, span {first_char}..{last_char} is out of range")
    start = starts[first_char]
    end = ends[last_char]
    following = [t for t in starts[last_char + 1:] if t > end]
    limit = min(following) if following else duration
    return start, min(end + decay, limit)


def cut_line_by_alignment(
    source: Path,
    output: Path,
    starts: list[float],
    ends: list[float],
    first_char: int,
    last_char: int,
    threshold_db: float,
    sample_rate: int,
    channels: int,
    lead: float = 0.05,
    search: float = 0.6,
) -> tuple[float, float]:
    """Cut one line out of a longer render, ending in the silence that follows it.

    The alignment gives where the line's last character ends; the actual sound
    decays for a while after that and the next utterance may start earlier
    than its alignment says. So the cut end is the first silence (below
    ``threshold_db``, at least 40 ms) found between the last character's end
    and ``search`` seconds later, plus a small margin; without any silence the
    cut stops just before the next character starts. Returns the span.
    """
    if not 0 <= first_char <= last_char < len(ends):
        raise CliError(f"alignment has {len(ends)} characters, span {first_char}..{last_char} is out of range")
    duration = probe(source).duration
    start = max(0.0, starts[first_char] - lead)
    char_end = ends[last_char]
    following = [t for t in starts[last_char + 1:] if t > char_end + 0.02]
    next_start = min(following) if following else duration
    end = boundary_after(source, char_end, next_start, threshold_db, search)
    cut_span(source, output, start, end, sample_rate, channels)
    return start, end


def cut_lines_by_alignment(
    source: Path,
    spans: list[tuple[int, int]],
    starts: list[float],
    ends: list[float],
    threshold_db: float,
    sample_rate: int,
    channels: int,
    workdir: Path,
    lead: float = 0.05,
    min_pause: float = 0.04,
) -> list[Path]:
    """Cut every line of one take into its own clip, at the pauses between the lines.

    ``spans`` are the (first, last) character indexes of each line in the
    alignment. A line ends 30 ms into the first silence (below ``threshold_db``,
    at least ``min_pause``) that reaches past its last character and begins
    before the next line's first character; it starts ``lead`` seconds before
    its first character, never inside the previous clip. No such silence means
    the model ran two lines together, which is an error: a cut inside speech
    is never made. The last line runs to the end of the take (or to the
    silence before a sacrificial tail). Returns ``001.wav``, ``002.wav``...
    """
    if not spans:
        raise CliError("no lines to cut")
    for first, last in spans:
        if not 0 <= first <= last < len(ends):
            raise CliError(f"alignment has {len(ends)} characters, span {first}..{last} is out of range")
    duration = probe(source).duration
    silences = detect_silences(source, threshold_db, min_pause)
    workdir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    previous_end = 0.0
    for index, (first, last) in enumerate(spans):
        char_start = starts[first]
        char_end = ends[last]
        next_char = starts[spans[index + 1][0]] if index + 1 < len(spans) else duration
        pauses = [g for g in silences if g.end > char_end and g.start < next_char]
        if pauses:
            end = min(pauses[0].start + 0.03, next_char)
        elif index + 1 < len(spans):
            raise CliError(
                f"no pause between line {index + 1} and line {index + 2}: the model ran them together "
                f"(nothing under {threshold_db:g} dB for {min_pause * 1000:.0f} ms between {char_end:.2f} s and {next_char:.2f} s). "
                "Give the line a full stop, a break or a paragraph of its own and render again."
            )
        else:
            end = duration
        start = max(previous_end, char_start - lead)
        clip = workdir / f"{index + 1:03d}.wav"
        cut_span(source, clip, start, end, sample_rate, channels)
        clips.append(clip)
        previous_end = end
    return clips


def boundary_after(source: Path, spoken_end: float, next_start: float, threshold_db: float, search: float = 0.6) -> float:
    """Where to cut after an utterance that ends near ``spoken_end`` (an alignment or segment time,
    which may sit slightly before or after the acoustic end) and before ``next_start``.

    The first silence (below ``threshold_db``, at least 40 ms) that reaches past
    ``spoken_end`` marks the boundary, whether it began a little before it or
    after; the cut lands 30 ms into that silence. Without any silence, the cut
    stops just before the next utterance starts.
    """
    duration = probe(source).duration
    # segments of consecutive lines touch exactly and alignment times are approximate, so the next
    # utterance's nominal start must not bound the search: the first real silence after the line is the boundary
    window_end = min(spoken_end + search, duration)
    silences = [g for g in detect_silences(source, threshold_db, 0.04) if g.end > spoken_end and g.start < window_end]
    if silences:
        return min(silences[0].start + 0.03, window_end)
    return min(max(next_start - 0.02, spoken_end + 0.05), duration)


# --- mix -----------------------------------------------------------------------


@dataclass(frozen=True)
class Bed:
    path: str
    gain_db: float  # applied to the bed before mixing (negative = quieter than the speech)


def mix(
    speech: Path,
    beds: list[Bed],
    output: Path,
    workdir: Path,
    sample_rate: int,
    channels: int | None,
    fade_in: float,
    fade_out: float,
    duck_db: float | None,
    lufs: float | None,
) -> Probe:
    """Lay one or more beds under ``speech`` for the length of the speech.

    Each bed is looped or cut to the speech length, faded in and out, set to
    its gain, optionally ducked while the speech is present (``duck_db`` is
    how far, in dB, the bed drops under speech), then summed with the speech.
    Run it after ``join`` and ``verify``: the result no longer has measurable gaps.
    """
    require_ffmpeg()
    if not beds:
        raise CliError("mix needs at least one --bed")
    if fade_in < 0 or fade_out < 0:
        raise CliError("fades must be zero or positive")
    workdir.mkdir(parents=True, exist_ok=True)
    layout = widest_layout([speech] + [Path(b.path) for b in beds], channels)
    voice = workdir / "speech.wav"
    length = decode_to_wav(speech, voice, sample_rate, layout).duration
    if fade_in + fade_out > length:
        raise CliError(f"fades ({fade_in} + {fade_out} s) exceed the speech length ({length:.1f} s)")

    inputs = ["-i", str(voice)]
    chains: list[str] = []
    bed_labels: list[str] = []
    for index, bed in enumerate(beds, start=1):
        wav = workdir / f"bed{index}.wav"
        info = decode_to_wav(Path(bed.path), wav, sample_rate, layout)
        inputs += ["-i", str(wav)]
        loop_size = int(info.duration * sample_rate)
        chain = f"[{index}:a]aloop=loop=-1:size={loop_size},atrim=0:{length:.6f},asetpts=PTS-STARTPTS"
        if fade_in > 0:
            chain += f",afade=t=in:st=0:d={fade_in:.6f}"
        if fade_out > 0:
            chain += f",afade=t=out:st={max(0.0, length - fade_out):.6f}:d={fade_out:.6f}"
        chain += f",volume={bed.gain_db:+.2f}dB[b{index}]"
        chains.append(chain)
        bed_labels.append(f"[b{index}]")
    if len(beds) > 1:
        chains.append(f"{''.join(bed_labels)}amix=inputs={len(beds)}:duration=longest:normalize=0[bed]")
    else:
        chains.append("[b1]anull[bed]")
    fmt = f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts={'mono' if layout == 1 else 'stereo'}"
    if duck_db is not None:
        if duck_db <= 0:
            raise CliError(f"--duck must be positive dB, got {duck_db}")
        # sidechaincompress needs both inputs in one sample format; ratio grows with the requested drop
        ratio = max(1.5, min(20.0, duck_db / 2))
        chains.append(f"[0:a]{fmt},asplit[v][sc]")
        chains.append(f"[bed]{fmt}[bedf]")
        chains.append(f"[bedf][sc]sidechaincompress=threshold=0.02:ratio={ratio:.2f}:attack=150:release=900:makeup=1,{fmt}[bedd]")
        chains.append("[v][bedd]amix=inputs=2:duration=first:normalize=0[out]")
    else:
        chains.append("[0:a][bed]amix=inputs=2:duration=first:normalize=0[out]")
    mixed = workdir / "mixed.wav"
    ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", "[out]", *INTERMEDIATE, str(mixed))
    encode(mixed, output, sample_rate, layout, lufs)
    return probe(output)
