from pathlib import Path

import pytest

from elevenlabs_cli.audio import JoinItem, parse_spec_args, parse_spec_lines, validate_items
from elevenlabs_cli.errors import CliError


def test_parse_lines_relative_to_spec(tmp_path: Path) -> None:
    items = parse_spec_lines(["# intro", "file a.wav", "", "silence 2.5", "file /abs/b.wav"], tmp_path)
    assert items == [JoinItem(file=str(tmp_path / "a.wav")), JoinItem(silence=2.5), JoinItem(file="/abs/b.wav")]


def test_parse_lines_rejects_garbage(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="line 1"):
        parse_spec_lines(["pause 3"], tmp_path)
    with pytest.raises(CliError, match="positive"):
        parse_spec_lines(["silence 0"], tmp_path)


def test_parse_args() -> None:
    assert parse_spec_args(["a.wav", "silence:3", "b.wav"]) == [JoinItem(file="a.wav"), JoinItem(silence=3.0), JoinItem(file="b.wav")]
    with pytest.raises(CliError, match="number of seconds"):
        parse_spec_args(["silence:long"])


def test_validate_items_shape(tmp_path: Path) -> None:
    clip = tmp_path / "a.wav"
    clip.write_bytes(b"")
    with pytest.raises(CliError, match="empty"):
        validate_items([])
    with pytest.raises(CliError, match="start and end with a file"):
        validate_items([JoinItem(silence=1.0), JoinItem(file=str(clip))])
    with pytest.raises(CliError, match="consecutive silence/overlap"):
        validate_items([JoinItem(file=str(clip)), JoinItem(silence=1.0), JoinItem(silence=1.0), JoinItem(file=str(clip))])
    with pytest.raises(CliError, match="not found"):
        validate_items([JoinItem(file=str(tmp_path / "missing.wav"))])
