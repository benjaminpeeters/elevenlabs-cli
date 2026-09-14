"""ffmpeg-backed audio operations: probe, trim, silence, join, measure, encode.

Timing is exact by construction: clips are trimmed to their voiced content
(keeping ``margin_ms`` of quiet audio on each side so onsets survive), the
digital silence inserted between two clips is shortened by the margins they
keep, and ``measure_gaps`` re-reads the result with the same threshold so a
``verify`` compares what a listener hears with what the spec asked for.

Intermediates are 16-bit WAV; the final container is chosen by the output
extension.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import CliError

ENCODERS: dict[str, list[str]] = {
    ".wav": ["-c:a", "pcm_s16le"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    ".opus": ["-c:a", "libopus", "-b:a", "96k"],
    ".flac": ["-c:a", "flac"],
}


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise CliError(f"{tool} not found on PATH; install ffmpeg to use trim, join, verify and clips")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise CliError(f"{args[0]} failed ({result.returncode}): {' '.join(args)}\n{result.stderr.strip()}")
    return result


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
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels:format=duration",
            "-of",
            "json",
            str(path),
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


@dataclass(frozen=True)
class TrimResult:
    source: str
    output: str
    voiced_start: float  # in the source
    voiced_end: float
    kept_leading: float  # quiet audio kept before the voiced content, seconds
    kept_trailing: float
    duration: float  # of the output


EDGE_MIN_SILENCE = 0.01  # seconds; leading/trailing quiet this short is still cut, so a gap never inherits it


def decode_to_wav(source: Path, target: Path) -> Probe:
    """Decode any container to 16-bit WAV so every later timestamp is exact.

    Compressed containers (MP3 in particular) report a header duration that
    differs from the decoded length by tens of milliseconds.
    """
    if not source.is_file():
        raise CliError(f"audio file not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-c:a", "pcm_s16le", str(target)])
    return probe(target)


def trim(source: Path, output: Path, threshold_db: float, margin_ms: int) -> TrimResult:
    """Cut ``source`` to its voiced content plus up to ``margin_ms`` on each side."""
    decoded = output.with_name(output.stem + "_decoded.wav")
    info = decode_to_wav(source, decoded)
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
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(decoded),
            "-af", f"atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS",
            "-ar", str(info.sample_rate), "-ac", str(info.channels), "-c:a", "pcm_s16le",
            str(output),
        ]
    )
    decoded.unlink()
    return TrimResult(
        source=str(source),
        output=str(output),
        voiced_start=voiced_start,
        voiced_end=voiced_end,
        kept_leading=voiced_start - start,
        kept_trailing=end - voiced_end,
        duration=end - start,
    )


def silence(output: Path, seconds: float, sample_rate: int, channels: int) -> None:
    """Write ``seconds`` of digital silence as WAV."""
    require_ffmpeg()
    if seconds <= 0:
        raise CliError(f"silence length must be positive, got {seconds}")
    layout = "mono" if channels == 1 else "stereo"
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={layout}",
            "-t", f"{seconds:.6f}", "-c:a", "pcm_s16le", str(output),
        ]
    )


@dataclass(frozen=True)
class JoinItem:
    """One entry of a join spec: a file path or a silence length in seconds."""

    file: str | None = None
    silence: float | None = None


@dataclass(frozen=True)
class PlacedClip:
    file: str
    start: float
    end: float
    kept_leading: float
    kept_trailing: float


@dataclass(frozen=True)
class ExpectedGap:
    start: float  # where the voiced content of the previous clip ends
    end: float  # where the voiced content of the next clip starts
    expected: float  # the silence asked for in the spec


@dataclass(frozen=True)
class Timing:
    output: str
    threshold_db: float
    clips: list[PlacedClip]
    gaps: list[ExpectedGap]
    duration: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


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
            items.append(JoinItem(file=str((base / rest).expanduser()) if not Path(rest).expanduser().is_absolute() else str(Path(rest).expanduser())))
        elif keyword == "silence" and rest:
            items.append(JoinItem(silence=parse_seconds(rest, f"line {number}")))
        else:
            raise CliError(f"spec line {number}: expected 'file <path>' or 'silence <seconds>', got '{line}'")
    return items


def parse_spec_args(args: list[str]) -> list[JoinItem]:
    """Positional items: a path, or ``silence:<seconds>``."""
    items: list[JoinItem] = []
    for arg in args:
        if arg.startswith("silence:"):
            items.append(JoinItem(silence=parse_seconds(arg[len("silence:"):], arg)))
        else:
            items.append(JoinItem(file=str(Path(arg).expanduser())))
    return items


def parse_seconds(text: str, where: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise CliError(f"{where}: silence must be a number of seconds, got '{text}'") from exc
    if value <= 0:
        raise CliError(f"{where}: silence must be positive, got {value}")
    return value


def validate_items(items: list[JoinItem]) -> None:
    if not items:
        raise CliError("join spec is empty")
    if items[0].file is None or items[-1].file is None:
        raise CliError("a join spec must start and end with a file")
    for previous, current in zip(items, items[1:]):
        if previous.silence is not None and current.silence is not None:
            raise CliError("two consecutive silences in the join spec; merge them into one")
    for item in items:
        if item.file is not None and not Path(item.file).is_file():
            raise CliError(f"join: file not found: {item.file} (input paths are relative to the current directory; use the absolute paths printed by tts)")


def join(
    items: list[JoinItem],
    output: Path,
    workdir: Path,
    threshold_db: float,
    margin_ms: int,
    do_trim: bool,
    lufs: float | None,
) -> Timing:
    """Assemble clips and exact silences into ``output`` and return the timing."""
    require_ffmpeg()
    validate_items(items)
    workdir.mkdir(parents=True, exist_ok=True)
    first = probe(Path(next(item.file for item in items if item.file)))
    sample_rate, channels = first.sample_rate, first.channels

    prepared: list[tuple[str, TrimResult | None, float | None]] = []  # (wav path, trim info, silence)
    for index, item in enumerate(items):
        if item.file is not None:
            wav = workdir / f"{index:03d}_clip.wav"
            if do_trim:
                result = trim(Path(item.file), wav, threshold_db, margin_ms)
            else:
                duration = decode_to_wav(Path(item.file), wav).duration
                result = TrimResult(item.file, str(wav), 0.0, duration, 0.0, 0.0, duration)
            prepared.append((str(wav), result, None))
        else:
            prepared.append(("", None, item.silence))

    # Shorten each silence by the quiet margins its neighbours keep, so the
    # gap between voiced content equals the spec.
    sequence: list[str] = []
    clips: list[PlacedClip] = []
    gaps: list[ExpectedGap] = []
    cursor = 0.0
    for index, (wav, info, secs) in enumerate(prepared):
        if info is not None:
            clips.append(PlacedClip(info.source, cursor, cursor + info.duration, info.kept_leading, info.kept_trailing))
            sequence.append(wav)
            cursor += info.duration
            continue
        assert secs is not None
        previous = prepared[index - 1][1]
        following = prepared[index + 1][1]
        assert previous is not None and following is not None
        digital = secs - previous.kept_trailing - following.kept_leading
        if digital <= 0:
            raise CliError(
                f"silence of {secs} s is shorter than the quiet margins around it "
                f"({previous.kept_trailing:.3f} + {following.kept_leading:.3f} s); lower trim_margin_ms or ask for a longer silence"
            )
        gap_wav = workdir / f"{index:03d}_silence.wav"
        silence(gap_wav, digital, sample_rate, channels)
        gaps.append(ExpectedGap(cursor - previous.kept_trailing, cursor + digital + following.kept_leading, secs))
        sequence.append(str(gap_wav))
        cursor += digital

    concat_list = workdir / "concat.txt"
    concat_list.write_text("".join(f"file '{p}'\n" for p in sequence), encoding="utf-8")
    joined = workdir / "joined.wav"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c:a", "pcm_s16le", str(joined)])
    encode(joined, output, lufs)
    return Timing(str(output), threshold_db, clips, gaps, probe(output).duration)


def encode(source: Path, output: Path, lufs: float | None) -> None:
    """Encode ``source`` into the container chosen by ``output``'s extension."""
    require_ffmpeg()
    ext = output.suffix.lower()
    if ext not in ENCODERS:
        raise CliError(f"unsupported output extension '{ext}' (supported: {', '.join(ENCODERS)})")
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if lufs is not None:
        args += ["-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11"]
    args += ENCODERS[ext] + [str(output)]
    run(args)


def decode_api_audio(data: bytes, api_format: str, output: Path, workdir: Path) -> None:
    """Write bytes returned by the API as ``output`` (raw copy when the container matches)."""
    ext = output.suffix.lower()
    family = api_format.split("_")[0]
    raw_ext = {"mp3": ".mp3", "opus": ".opus", "wav": ".wav"}.get(family)
    output.parent.mkdir(parents=True, exist_ok=True)
    if raw_ext == ext:
        output.write_bytes(data)
        return
    require_ffmpeg()
    workdir.mkdir(parents=True, exist_ok=True)
    if family in ("pcm", "ulaw", "alaw"):
        rate = api_format.split("_")[1]
        codec = {"pcm": "s16le", "ulaw": "mulaw", "alaw": "alaw"}[family]
        source = workdir / f"api.{codec}"
        source.write_bytes(data)
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", codec, "-ar", rate, "-ac", "1", "-i", str(source)]
    else:
        source = workdir / f"api{raw_ext}"
        source.write_bytes(data)
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if ext not in ENCODERS:
        raise CliError(f"unsupported output extension '{ext}' (supported: {', '.join(ENCODERS)})")
    run(args + ENCODERS[ext] + [str(output)])


@dataclass(frozen=True)
class GapCheck:
    index: int
    expected: float
    measured: float | None
    ok: bool


def verify_gaps(path: Path, expected: list[float], threshold_db: float, tolerance_ms: int) -> list[GapCheck]:
    """Measure every internal silence in ``path`` and compare it with ``expected`` in order.

    A silence is any stretch below the threshold at least a third as long as
    the shortest expected gap (so the small quiet stretches inside speech are
    ignored). Leading and trailing silence of the file is not a gap.
    """
    if not expected:
        raise CliError("nothing to verify: the spec has no silences")
    shortest = min(expected)
    total = probe(path).duration
    found = detect_silences(path, threshold_db, max(0.05, shortest / 3))
    inner = [g for g in found if g.start > 0.001 and g.end < total - 0.001]
    tolerance = tolerance_ms / 1000.0
    checks: list[GapCheck] = []
    for index, want in enumerate(expected):
        measured = inner[index].length if index < len(inner) else None
        ok = measured is not None and abs(measured - want) <= tolerance
        checks.append(GapCheck(index, want, measured, ok))
    for extra in inner[len(expected):]:
        checks.append(GapCheck(len(checks), 0.0, extra.length, False))
    return checks
