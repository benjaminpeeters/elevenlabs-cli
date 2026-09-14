import json
from pathlib import Path

import pytest

from elevenlabs_cli import lab
from elevenlabs_cli.errors import CliError


def make_lab(tmp_path: Path) -> Path:
    root = tmp_path / "lab"
    (root / "protocols" / "en-dialogue-elderly").mkdir(parents=True)
    (root / "protocols" / "en-dialogue-elderly" / "isolation.txt").write_text("Two winters in Sanaa, back in the nineties.\n")
    return root


def trial(voice: str, name: str, use_case: str, protocol: str, verdict: int | None, day: str = "2026-09-14", model: str = "eleven_v3", settings: dict | None = None) -> lab.Trial:
    settings = settings or {"stability": 0.35}
    tid = lab.make_trial_id(day, use_case, protocol, model, settings)
    return lab.Trial(tid, voice, name, {"age": "old"}, "en", use_case, protocol, f"{use_case}/{protocol}#abcd1234", model, "2.68.0", settings, 7, day, f"{tid}.m4a", {"integrated_lufs": -18.0}, verdict, "")


def test_protocol_id_changes_with_content(tmp_path: Path) -> None:
    root = make_lab(tmp_path)
    first = lab.load_protocol(root, "en-dialogue-elderly", "isolation")
    assert first.id.startswith("en-dialogue-elderly/isolation#") and len(first.id.split("#")[1]) == 8
    (root / "protocols" / "en-dialogue-elderly" / "isolation.txt").write_text("Different words.\n")
    assert lab.load_protocol(root, "en-dialogue-elderly", "isolation").id != first.id
    with pytest.raises(CliError, match="no protocol en-dialogue-elderly/longform"):
        lab.load_protocol(root, "en-dialogue-elderly", "longform")
    assert lab.list_protocols(root) == ["en-dialogue-elderly/isolation"]


def test_trial_id_and_paths() -> None:
    tid = lab.make_trial_id("2026-09-14", "en-dialogue-elderly", "isolation", "eleven_v3", {"stability": 0.35, "tag": "warmly"})
    assert tid == "2026-09-14-en-dialogue-elderly-isolation-eleven-v3-st0.35-tawarmly"
    audio_path, json_path = lab.trial_paths(Path("/lab"), "voiceX", tid)
    assert audio_path.name == tid + ".m4a" and json_path.parent.name == "voiceX"


def test_save_load_rate_index_shortlist(tmp_path: Path) -> None:
    root = make_lab(tmp_path)
    a1 = trial("vA", "Ada", "en-dialogue-elderly", "isolation", 4)
    a2 = trial("vA", "Ada", "en-dialogue-elderly", "contrast", 5)
    b1 = trial("vB", "Bea", "en-dialogue-elderly", "isolation", 5)
    c1 = trial("vC", "Cy", "en-narration", "isolation", 3)
    unrated = trial("vD", "Dee", "en-dialogue-elderly", "isolation", None, model="eleven_multilingual_v2", settings={"stability": 0.5, "speed": 0.9})
    for t in (a1, a2, b1, c1, unrated):
        lab.save_trial(root, t)
    assert len(lab.load_trials(root)) == 5
    path, found = lab.find_trial(root, unrated.trial_id)
    assert found.verdict is None
    found.verdict = 2
    found.note = "flat"
    path.write_text(found.to_json())
    index = lab.rebuild_index(root)
    assert set(index["voices"]) == {"vA", "vB", "vC", "vD"}
    assert index["voices"]["vA"]["best"]["en-dialogue-elderly"] == {"verdict": 5, "protocols": {"isolation": 4, "contrast": 5}}
    assert json.loads((root / "index.json").read_text())["voices"]["vD"]["trials"][0]["verdict"] == 2
    ranked = lab.shortlist(root, "en-dialogue-elderly", "en")
    assert [r["name"] for r in ranked] == ["Ada", "Bea", "Dee"]  # 5 on two protocols beats 5 on one; 2 last
    assert lab.shortlist(root, "en-narration", None)[0]["name"] == "Cy"
    with pytest.raises(CliError, match="0 trial files match"):
        lab.find_trial(root, "nope")
