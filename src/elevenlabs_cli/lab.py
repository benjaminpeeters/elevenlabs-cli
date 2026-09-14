"""The voice lab: trial files, protocol ids, the derived index and shortlists.

Layout, under ``<config dir>/lab``:

- ``protocols/<use-case>/<name>.txt``: fixed audition scripts; a protocol id is
  ``<use-case>/<name>#<8 hex of the content hash>``, so an edited script is a
  new protocol and old trials stay comparable among themselves.
- ``samples/<voice-id>/<trial-id>.m4a`` + ``.json``: one rendered trial and
  everything about it. The JSON files are the source of truth.
- ``findings.json``: dated facts about the API and the pipeline.
- ``index.json``: derived from the trial files by ``rebuild_index``; never edited.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .errors import CliError


def lab_dir(config_path: Path) -> Path:
    return config_path.parent / "lab"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-").lower()


@dataclass(frozen=True)
class Protocol:
    use_case: str
    name: str
    id: str
    text: str


def load_protocol(lab: Path, use_case: str, name: str) -> Protocol:
    path = lab / "protocols" / use_case / f"{name}.txt"
    if not path.is_file():
        available = sorted(p.parent.name + "/" + p.stem for p in (lab / "protocols").glob("*/*.txt")) if (lab / "protocols").is_dir() else []
        raise CliError(f"no protocol {use_case}/{name} in {lab / 'protocols'}; available: {', '.join(available) or 'none'}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise CliError(f"protocol {path} is empty")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return Protocol(use_case, name, f"{use_case}/{name}#{digest}", text)


def list_protocols(lab: Path) -> list[str]:
    root = lab / "protocols"
    if not root.is_dir():
        return []
    return sorted(p.parent.name + "/" + p.stem for p in root.glob("*/*.txt"))


@dataclass
class Trial:
    trial_id: str
    voice_id: str
    voice_name: str
    voice_labels: dict[str, str]
    language: str | None
    use_case: str
    protocol: str
    protocol_id: str
    model: str
    sdk_version: str
    settings: dict[str, Any]
    seed: int | None
    date: str
    sample: str  # file name next to the JSON
    measurements: dict[str, Any] = field(default_factory=dict)
    verdict: int | None = None
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"


def settings_slug(model: str, settings: dict[str, Any]) -> str:
    parts = []
    for key in ("stability", "speed", "similarity", "style", "tag"):
        value = settings.get(key)
        if value is not None and value != "":
            parts.append(f"{key[:2]}{value}")
    return "-".join(parts) or "default"


def make_trial_id(day: str, use_case: str, protocol: str, model: str, settings: dict[str, Any]) -> str:
    return slug(f"{day}-{use_case}-{protocol}-{model}-{settings_slug(model, settings)}")


def trial_paths(lab: Path, voice_id: str, trial_id: str, ext: str = ".m4a") -> tuple[Path, Path]:
    folder = lab / "samples" / voice_id
    return folder / f"{trial_id}{ext}", folder / f"{trial_id}.json"


def save_trial(lab: Path, trial: Trial) -> Path:
    _, json_path = trial_paths(lab, trial.voice_id, trial.trial_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(trial.to_json(), encoding="utf-8")
    return json_path


def load_trials(lab: Path) -> list[Trial]:
    trials: list[Trial] = []
    root = lab / "samples"
    if not root.is_dir():
        return trials
    for path in sorted(root.glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            trials.append(Trial(**data))
        except (json.JSONDecodeError, TypeError) as exc:
            raise CliError(f"unreadable trial file {path}: {exc}") from exc
    return trials


def find_trial(lab: Path, trial_id: str) -> tuple[Path, Trial]:
    matches = list((lab / "samples").glob(f"*/{trial_id}.json")) if (lab / "samples").is_dir() else []
    if len(matches) != 1:
        raise CliError(f"{len(matches)} trial files match '{trial_id}' (expected exactly one)")
    return matches[0], Trial(**json.loads(matches[0].read_text(encoding="utf-8")))


def rebuild_index(lab: Path) -> dict[str, Any]:
    """One entry per voice: card, trials, and the best verdict per use case."""
    voices: dict[str, dict[str, Any]] = {}
    for trial in load_trials(lab):
        card = voices.setdefault(trial.voice_id, {
            "voice_id": trial.voice_id, "name": trial.voice_name, "labels": trial.voice_labels, "language": trial.language,
            "trials": [], "best": {},
        })
        card["trials"].append({
            "trial_id": trial.trial_id, "use_case": trial.use_case, "protocol": trial.protocol, "model": trial.model,
            "settings": trial.settings, "date": trial.date, "verdict": trial.verdict, "note": trial.note,
            "measurements": trial.measurements,
        })
        if trial.verdict is not None:
            best = card["best"].setdefault(trial.use_case, {"verdict": 0, "protocols": {}})
            best["verdict"] = max(best["verdict"], trial.verdict)
            best["protocols"][trial.protocol] = max(best["protocols"].get(trial.protocol, 0), trial.verdict)
    index = {"generated": date.today().isoformat(), "voices": voices}
    (lab / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


def shortlist(lab: Path, use_case: str, language: str | None) -> list[dict[str, Any]]:
    """Voices rated on ``use_case``, best first; ties broken by how many protocols they were rated on."""
    index = rebuild_index(lab)
    rows = []
    for card in index["voices"].values():
        best = card["best"].get(use_case)
        if best is None:
            continue
        if language and card.get("language") and card["language"] != language:
            continue
        protocols = best["protocols"]
        rows.append({
            "voice_id": card["voice_id"], "name": card["name"], "verdict": best["verdict"],
            "protocols_rated": len(protocols), "per_protocol": protocols,
            "trials": len([t for t in card["trials"] if t["use_case"] == use_case]),
        })
    return sorted(rows, key=lambda r: (-r["verdict"], -r["protocols_rated"], r["name"]))
