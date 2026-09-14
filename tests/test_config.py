import json
from pathlib import Path

import pytest

from elevenlabs_cli.cli import main
from elevenlabs_cli.config import Config, check_ranges, coerce, read_key_file, validate
from elevenlabs_cli.errors import CliError


def test_validate_requires_every_key() -> None:
    with pytest.raises(CliError, match="missing keys"):
        validate({"api_key_env": "X"})


def test_validate_rejects_unknown_key(config_file: Path) -> None:
    raw = json.loads(config_file.read_text())
    raw["colour"] = "blue"
    with pytest.raises(CliError, match="unknown config key 'colour'"):
        validate(raw)


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("confirm_above_chars", "many", "must be an integer"),
        ("confirm_above_chars", -1, "non-negative"),
        ("trim_threshold_db", "loud", "must be a number"),
        ("voices", {"en": "not-a-dict"}, "alias -> voice id"),
        ("voices", {"en": {"narrator": ""}}, "non-empty voice id"),
        ("default_model", "", "non-empty string"),
        ("sample_rate", 44000, "must be one of"),
        ("channels", 3, "must be 1 or 2"),
    ],
)
def test_coerce_errors(key: str, value: object, message: str) -> None:
    with pytest.raises(CliError, match=message):
        check_ranges(key, coerce(key, value))


def test_coerce_from_strings() -> None:
    assert coerce("confirm_above_chars", "2500") == 2500
    assert coerce("default_lufs", "-16") == -16.0
    assert coerce("default_lufs", "null") is None
    assert coerce("api_key_file", "null") is None
    assert coerce("output_dir", "null") is None
    assert coerce("output_dir", "~/audio") == "~/audio"


def test_missing_config_explains_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_CLI_CONFIG", str(tmp_path / "none.json"))
    with pytest.raises(CliError, match="config init"):
        Config.load()


def test_key_from_env_wins(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_from_env")
    assert Config.load().api_key() == "sk_from_env"


def test_key_from_bare_file(config_file: Path) -> None:
    assert Config.load().api_key() == "sk_test_not_a_real_key"


def test_key_from_env_style_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# comment\nOTHER=1\nexport ELEVENLABS_API_KEY='sk_quoted'\n")
    assert read_key_file(path, "ELEVENLABS_API_KEY") == "sk_quoted"
    path.write_text("OTHER=1\n")
    with pytest.raises(CliError, match="no 'ELEVENLABS_API_KEY=' line"):
        read_key_file(path, "ELEVENLABS_API_KEY")


def test_key_missing_everywhere(config_file: Path) -> None:
    config = Config.load()
    config.set("api_key_file", None)
    with pytest.raises(CliError, match="API key not found"):
        config.api_key()


def test_alias_resolution(config_file: Path) -> None:
    config = Config.load()
    config.set("voices", {"en": {"narrator": "id_en", "solo": "id_solo"}, "fr": {"narrator": "id_fr"}})
    assert config.resolve_alias("solo", None) == "id_solo"
    assert config.resolve_alias("narrator", "fr") == "id_fr"
    assert config.resolve_alias("nobody", None) is None
    with pytest.raises(CliError, match="several languages"):
        config.resolve_alias("narrator", None)


def test_cli_config_set_get_show(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "set", "confirm_above_chars", "10"]) == 0
    assert main(["config", "set", "voices.en.narrator", "abc123"]) == 0
    assert main(["config", "get", "voices.en.narrator"]) == 0
    assert capsys.readouterr().out.strip().endswith("abc123")
    assert main(["config", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["confirm_above_chars"] == 10
    assert shown["voices"] == {"en": {"narrator": "abc123"}}
    assert "sk_" not in json.dumps(shown)
    assert main(["config", "set", "voices.en.narrator", "null"]) == 0
    capsys.readouterr()
    assert main(["config", "get", "voices"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_cli_config_set_rejects_bad_value(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "set", "confirm_above_chars", "lots"]) == 1
    assert "must be an integer" in capsys.readouterr().err


def test_cli_config_init_from_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "new" / "config.json"
    key = tmp_path / "k.env"
    key.write_text("ELEVENLABS_API_KEY=sk_x\n")
    monkeypatch.setenv("ELEVENLABS_CLI_CONFIG", str(target))
    assert main(["config", "init", "--api-key-file", str(key), "--output-dir", str(tmp_path / "audio")]) == 0
    values = json.loads(target.read_text())
    assert values["api_key_file"] == str(key)
    assert values["default_model"] == "eleven_v3"
    assert values["sample_rate"] == 48000 and values["channels"] == 1
    assert main(["config", "init"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_config_set_on_read_only_folder_is_loud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ELEVENLABS_CLI_CONFIG", "/proc/nope/config.json")
    assert main(["config", "init", "--api-key-env", "X"]) == 1
    assert "cannot write to /proc/nope" in capsys.readouterr().err
