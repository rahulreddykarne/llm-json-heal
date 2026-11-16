import pytest

from llm_json_heal import clamp, ensure_list, pick, to_percentage


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, []),
        ("", []),
        ("  ", []),
        ("a, b ,c", ["a", "b", "c"]),
        ("single", ["single"]),
        (["x"], ["x"]),
        (("x", "y"), ["x", "y"]),
        (5, [5]),
    ],
)
def test_ensure_list(value, expected):
    assert ensure_list(value) == expected


def test_ensure_list_split_disabled():
    assert ensure_list("a, b", split="") == ["a, b"]


@pytest.mark.parametrize(
    "value, expected",
    [(0.85, 85), ("0.85", 85), (85, 85), ("92%", 92), (1, 100), (150, 100), (-3, 0), (0, 0), ("n/a", 0), (None, 0), (True, 0)],
)
def test_to_percentage(value, expected):
    assert to_percentage(value) == expected


def test_clamp():
    assert clamp("7.53/10", 0, 10, ndigits=1) == 7.5
    assert clamp(12, 0, 10) == 10
    assert clamp("x", 0, 10, default=0.0) == 0.0
    assert clamp(float("nan"), 0, 10) is None


SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


@pytest.mark.parametrize(
    "value, expected",
    [("high", "HIGH"), ("High risk", "HIGH"), ("critical!", "CRITICAL"), ("Highway", None), (None, None)],
)
def test_pick(value, expected):
    assert pick(value, SEVERITIES) == expected


def test_pick_default_and_case_sensitive():
    assert pick("unknown", SEVERITIES, default="MEDIUM") == "MEDIUM"
    assert pick("high", SEVERITIES, case_sensitive=True) is None
