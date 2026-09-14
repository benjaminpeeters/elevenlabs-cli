import pytest

from elevenlabs_cli.cost import billable_chars, chunk_text, estimate_credits, model_info
from elevenlabs_cli.errors import CliError


def test_pause_tags_are_free() -> None:
    assert billable_chars('Hello [pause] there <break time="1.5s"/> friend') == len("Hello  there  friend")


def test_estimate_uses_model_rate() -> None:
    assert estimate_credits("abcd", "eleven_v3") == 4
    assert estimate_credits("abcd", "eleven_flash_v2_5") == 2


def test_unknown_model_is_an_error() -> None:
    with pytest.raises(CliError, match="unknown model 'eleven_v9'"):
        model_info("eleven_v9")


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("  one\n\ntwo  ", "eleven_v3") == ["one\n\ntwo"]


def test_v3_chunks_by_paragraph() -> None:
    paragraph = "x" * 3000
    text = "\n\n".join([paragraph] * 3)
    chunks = chunk_text(text, "eleven_v3")
    assert chunks == [paragraph, paragraph, paragraph]


def test_v2_chunks_by_sentence() -> None:
    sentence = "word " * 1500 + "end."
    text = " ".join([sentence] * 3)
    chunks = chunk_text(text, "eleven_multilingual_v2")
    assert len(chunks) == 3
    assert all(len(c) <= 10000 for c in chunks)


def test_oversized_unit_is_an_error() -> None:
    with pytest.raises(CliError, match="single paragraph"):
        chunk_text("y" * 6000, "eleven_v3")


def test_empty_text_is_an_error() -> None:
    with pytest.raises(CliError, match="empty"):
        chunk_text("  \n ", "eleven_v3")
