import json
import shutil
from pathlib import Path

import pytest

from elevenlabs_cli.config import defaults


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A complete config in tmp_path, selected through the environment variable."""
    values = defaults()
    values["output_dir"] = str(tmp_path / "out")
    values["api_key_file"] = str(tmp_path / "key.txt")
    (tmp_path / "key.txt").write_text("sk_test_not_a_real_key\n")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values))
    monkeypatch.setenv("ELEVENLABS_CLI_CONFIG", str(path))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return path


def set_config(path: Path, **updates: object) -> None:
    values = json.loads(path.read_text())
    values.update(updates)
    path.write_text(json.dumps(values))


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
