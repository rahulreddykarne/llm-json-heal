import json

import pytest

from llm_json_heal import (
    JSONHealError,
    close_truncated_json,
    extract_json,
    find_complete_json,
    heal_json_string,
    loads,
    repair_json,
    sanitize_json_string,
)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def test_plain_json():
    assert loads('{"a": 1, "b": [1, 2]}') == {"a": 1, "b": [1, 2]}


def test_bytes_input():
    assert loads(b'{"a": 1}') == {"a": 1}


def test_fenced_json_with_prose():
    text = 'Here is the result:\n```json\n{"verdict": "VULNERABLE", "confidence": 90}\n```\nHope that helps!'
    assert loads(text) == {"verdict": "VULNERABLE", "confidence": 90}


def test_unlabelled_fence():
    assert loads("```\n{\"x\": true}\n```") == {"x": True}


def test_json_embedded_in_prose():
    assert loads('The answer is {"ok": true} as requested.') == {"ok": True}


def test_deeply_nested_object_in_prose_returns_outer_object():
    text = 'Result: {"a": {"b": {"c": {"d": 1}}}, "e": 2} done'
    assert loads(text) == {"a": {"b": {"c": {"d": 1}}}, "e": 2}


def test_braces_inside_strings_are_ignored():
    assert loads('x {"code": "if (a) { b(); }", "n": 1} y') == {"code": "if (a) { b(); }", "n": 1}


def test_skips_non_json_braces_before_payload():
    assert loads('Use {curly} braces. {"a": 1}') == {"a": 1}


def test_markdown_link_and_citation_not_mistaken_for_payload():
    assert loads('See [here](http://x) and [1]. {"a": 1}') == {"a": 1}


def test_top_level_array_of_objects():
    assert loads('Findings:\n[{"id": 1}, {"id": 2}]') == [{"id": 1}, {"id": 2}]


def test_bare_numeric_array_when_whole_reply():
    assert loads("[1, 2, 3]") == [1, 2, 3]


def test_find_complete_json():
    assert find_complete_json('{"a": "}"} trailing') == '{"a": "}"}'
    assert find_complete_json("[1, [2]] x") == "[1, [2]]"
    assert find_complete_json('{"a": 1') is None
    assert find_complete_json("nope") is None


def test_extract_json_returns_none_without_payload():
    assert extract_json("no json here") is None


# --------------------------------------------------------------------------- #
# Healing
# --------------------------------------------------------------------------- #
def test_trailing_commas():
    assert loads('{"a": [1, 2,], "b": 3,}') == {"a": [1, 2], "b": 3}


def test_comma_inside_string_is_kept():
    assert loads('{"a": "x, }"}') == {"a": "x, }"}


def test_literal_newlines_and_tabs_in_strings():
    assert loads('{"reason": "line one\nline\ttwo"}') == {"reason": "line one\nline\ttwo"}


def test_control_characters_removed():
    assert loads('{"a": "b\x00c\x07"}') == {"a": "bc"}


def test_invalid_escape_sequences():
    assert loads(r'{"regex": "\d+\.\w", "path": "C:\Users\x"}') == {"regex": r"\d+\.\w", "path": r"C:\Users\x"}


def test_valid_escapes_preserved():
    assert loads(r'{"a": "quote \" slash \\ tab \t uni \u00e9"}') == {"a": 'quote " slash \\ tab \t uni \u00e9'}


def test_heal_json_string_is_noop_on_valid_json():
    doc = '{"a": [1, 2], "b": {"c": "d"}}'
    assert heal_json_string(doc) == doc


def test_sanitize_is_idempotent():
    doc = '{"a": "x\ny", "b": "\\d",}'
    once = sanitize_json_string(doc)
    assert sanitize_json_string(once) == once
    assert json.loads(once) == {"a": "x\ny", "b": "\\d"}


# --------------------------------------------------------------------------- #
# Error-driven repair
# --------------------------------------------------------------------------- #
def test_missing_comma_between_members():
    assert loads('{"a": 1 "b": 2 "c": "x" "d": true}') == {"a": 1, "b": 2, "c": "x", "d": True}


def test_missing_comma_between_array_objects():
    assert loads('[{"a": 1} {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_missing_colon():
    assert loads('{"key" "value"}') == {"key": "value"}


def test_unquoted_keys():
    assert loads("{verdict: \"ok\", score: 3}") == {"verdict": "ok", "score": 3}


def test_unescaped_quotes_inside_string():
    assert loads('{"msg": "he said "hi" ok", "n": 1}') == {"msg": 'he said "hi" ok', "n": 1}


def test_repair_json_returns_input_when_nothing_to_fix():
    s = '{"a": }'
    try:
        json.loads(s)
    except json.JSONDecodeError as exc:
        assert repair_json(s, exc) == s


# --------------------------------------------------------------------------- #
# Truncation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": "hello wor', {"a": "hello wor"}),
        ('{"a": 1, "b": [1, 2', {"a": 1, "b": [1, 2]}),
        ('{"a": 1, "b": {"c": 2,', {"a": 1, "b": {"c": 2}}),
        ('{"a": 1, "b":', {"a": 1, "b": None}),
        ('{"a": 1, "hel', {"a": 1}),
        ('{"a": 1, "b": tru', {"a": 1}),
        ('{"a": {', {"a": {}}),
    ],
)
def test_truncated_documents(text, expected):
    assert loads(text) == expected


def test_truncated_inside_fence():
    text = 'Answer:\n```json\n{"verdict": "VULNERABLE", "reasoning": "The input flows into'
    assert loads(text) == {"verdict": "VULNERABLE", "reasoning": "The input flows into"}


def test_truncated_does_not_return_inner_object():
    assert loads('{"outer": {"inner": 1}, "x": "cut') == {"outer": {"inner": 1}, "x": "cut"}


def test_close_truncated_json_result_is_valid():
    closed = close_truncated_json('[{"a": "x\\')
    assert closed is not None
    json.loads(closed)


def test_close_truncated_json_none_for_empty():
    assert close_truncated_json("") is None


# --------------------------------------------------------------------------- #
# Python literal fallback
# --------------------------------------------------------------------------- #
def test_python_style_dict():
    assert loads("{'a': True, 'b': None, 'c': 'it\\'s'}") == {"a": True, "b": None, "c": "it's"}


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", "   ", "no json at all", "just {curly} text"])
def test_failure_raises(text):
    with pytest.raises(JSONHealError):
        loads(text)


def test_failure_returns_default():
    assert loads("nothing", default={}) == {}
    assert loads("nothing", default=None) is None


def test_error_is_value_error_with_context():
    with pytest.raises(ValueError) as info:
        loads("nothing")
    assert info.value.text == "nothing"


def test_type_error_for_non_string():
    with pytest.raises(TypeError):
        loads(123)
