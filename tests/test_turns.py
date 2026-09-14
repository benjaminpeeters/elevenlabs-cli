import pytest

from elevenlabs_cli import turns
from elevenlabs_cli.errors import CliError

SCRIPT = """# a short exchange
Sam: Margaret! You came! I honestly wasn't sure anyone would show up on a Tuesday morning.
Margaret: [gap 1.2] Oh, Sam, I wouldn't miss it. Would I?
Sam: Never.
Margaret: [quick] Good. Now tell me what you have learned, properly, from the very start, without skipping the parts you find boring, because those are usually the parts that matter.
Sam: Okay...
Margaret: [overlap 0.4] Take your time.
Margaret: I have all morning.
Sam: Right.
"""


def test_parse_strips_markers_and_keeps_numbers() -> None:
    lines = turns.parse_script(SCRIPT)
    assert [l.speaker for l in lines] == ["Sam", "Margaret", "Sam", "Margaret", "Sam", "Margaret", "Margaret", "Sam"]
    assert lines[1].gap == 1.2 and lines[1].text.startswith("Oh, Sam")
    assert lines[3].quick and lines[3].text.startswith("Good.")
    assert lines[5].overlap == 0.4 and lines[5].text == "Take your time."
    assert lines[0].number == 2 and lines[1].number == 3


@pytest.mark.parametrize("bad,message", [
    ("Sam [gap 1] hello", "expected 'Name: text'"),
    ("Sam: [gap] hello", "malformed marker"),
    ("Sam: [overlap 0.3]", "nothing to say"),
    ("Sam: [quick] first line", "first line cannot carry"),
])
def test_parse_errors_name_the_line(bad: str, message: str) -> None:
    with pytest.raises(CliError, match=message):
        turns.parse_script(bad)


def test_plan_is_deterministic_per_seed_and_honours_markers() -> None:
    lines = turns.parse_script(SCRIPT)
    plan = turns.plan_turns(lines, 42, 0.15, 0.45)
    again = turns.plan_turns(lines, 42, 0.15, 0.45)
    other = turns.plan_turns(lines, 43, 0.15, 0.45)
    assert plan == again
    assert plan != other
    by_index = {t.index: t for t in plan}
    assert by_index[1] == turns.Turn(1, "gap", 1.2, "marker")
    assert by_index[3] == turns.Turn(3, "gap", turns.QUICK_GAP, "quick")
    assert by_index[5] == turns.Turn(5, "overlap", 0.4, "marker")


def test_plan_rules_and_ranges() -> None:
    lines = turns.parse_script(SCRIPT)
    by_index = {t.index: t for t in turns.plan_turns(lines, 7, 0.15, 0.45)}
    assert by_index[2].reason == "question" and 0.10 <= by_index[2].seconds <= 0.30  # after "Would I?"
    assert by_index[4].reason == "short-reply" and 0.10 <= by_index[4].seconds <= 0.25  # "Okay..." is one word: a quick reply wins
    assert by_index[6].reason == "same-speaker" and 0.40 <= by_index[6].seconds <= 0.80
    assert by_index[7].reason == "short-reply" and 0.10 <= by_index[7].seconds <= 0.25  # "Right."


def test_default_gap_stays_in_range() -> None:
    lines = turns.parse_script("A: This is a plain sentence of ordinary length for testing.\nB: And this is a plain reply of ordinary length as well.\n")
    for seed in range(20):
        turn = turns.plan_turns(lines, seed, 0.15, 0.45)[0]
        assert turn.reason == "default" and 0.15 <= turn.seconds <= 0.45


def test_seeds_are_stable_and_in_api_range() -> None:
    base = turns.script_seed(SCRIPT)
    assert base == turns.script_seed(SCRIPT)
    assert 0 <= base <= turns.API_SEED_MAX
    assert turns.speaker_seed(base, "Sam") != turns.speaker_seed(base, "Margaret")
    assert 0 <= turns.speaker_seed(base, "Sam") <= turns.API_SEED_MAX


def test_describe_rows() -> None:
    lines = turns.parse_script(SCRIPT)
    rows = turns.describe(lines, turns.plan_turns(lines, 1, 0.15, 0.45))
    assert rows[0][3] == "start" and rows[1][3] == "gap 1.20 s" and rows[1][4] == "marker"
    assert rows[5][3] == "overlap 0.40 s"


def test_long_statement_rule() -> None:
    lines = turns.parse_script(
        "A: " + "word " * 26 + "end.\nB: This reply is long enough not to count as short.\n"
        "A: Something and then it trails off...\nB: This reply is long enough not to count as short either.\n"
    )
    plan = turns.plan_turns(lines, 3, 0.15, 0.45)
    assert plan[0].reason == "long-statement" and 0.35 <= plan[0].seconds <= 0.70
    assert plan[2].reason == "long-statement"
