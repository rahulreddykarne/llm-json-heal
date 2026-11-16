"""Extract, heal and parse JSON from raw LLM output.

Language models wrap JSON in prose and Markdown fences, emit literal newlines
inside strings, leave trailing commas, forget commas and colons, use invalid
escape sequences such as ``\\d``, and get cut off mid-object when they hit a
token limit. The functions here undo those mistakes so the payload can be read
with the standard :mod:`json` module.

The only entry point most callers need is :func:`loads`.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "JSONHealError",
    "loads",
    "extract_json",
    "find_complete_json",
    "strip_control_characters",
    "heal_json_string",
    "sanitize_json_string",
    "repair_json",
    "close_truncated_json",
]

_MISSING = object()

# ```json ... ``` (or an unlabelled fence). The closing fence is optional so a
# reply that was cut off inside the fence is still found.
_FENCE_RE = re.compile(r"```[ \t]*(?:json5?|javascript|js)?[ \t]*\r?\n?(.*?)(?:```|\Z)", re.DOTALL | re.IGNORECASE)

# A '[' only counts as the start of JSON when it is followed by something that
# can open a JSON array element, so Markdown links like "[here](...)" and
# citations like "[1]" are not mistaken for payloads.
_ARRAY_START_RE = re.compile(r"\[\s*[\{\[\"\]]")

_IDENT_RE = re.compile(r"[A-Za-z_$][\w$-]*")

_VALID_ESCAPES = frozenset('"\\/bfnrt')
_HEX = frozenset("0123456789abcdefABCDEF")


class JSONHealError(ValueError):
    """Raised by :func:`loads` when no JSON value can be recovered.

    Attributes:
        text: The input that could not be parsed.
        candidate: The extracted JSON candidate, if one was found.
    """

    def __init__(self, message: str, text: str = "", candidate: Optional[str] = None):
        super().__init__(message)
        self.text = text
        self.candidate = candidate


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def loads(text: Any, *, default: Any = _MISSING, max_repairs: int = 20) -> Any:
    """Parse the JSON value contained in *text*.

    *text* may be a bare JSON document or a full LLM reply with prose around
    the payload, Markdown code fences, and common syntax mistakes. Recovery is
    attempted in this order:

    1. locate the payload (fenced block, then the first balanced object or
       array, then a truncated tail);
    2. heal it (control characters, literal newlines in strings, trailing
       commas, invalid escapes);
    3. repair individual decode errors (missing commas or colons, unquoted
       keys, unescaped quotes, trailing data), up to *max_repairs* times;
    4. close a truncated document (unterminated strings, open brackets);
    5. fall back to Python literal syntax (single quotes, ``True``/``None``).

    Args:
        text: The LLM output (``str``, ``bytes`` or ``bytearray``).
        default: Returned instead of raising when nothing can be recovered.
        max_repairs: Maximum number of error-driven repair passes.

    Returns:
        The decoded JSON value (usually a ``dict`` or ``list``).

    Raises:
        JSONHealError: If no JSON can be recovered and *default* is not given.
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", errors="replace")
    if not isinstance(text, str):
        raise TypeError(f"loads() expects str or bytes, got {type(text).__name__}")

    raw = _extract_raw(text)
    if raw is None:
        return _fail("no JSON object or array found in input", text, None, default)

    candidate = sanitize_json_string(raw)
    ok, value = _parse_with_repairs(candidate, max_repairs)
    if ok:
        return value

    # Python-style literals: {'a': True, 'b': None}
    ok, value = _try_literal_eval(raw)
    if ok:
        logger.debug("Parsed payload as a Python literal")
        return value

    return _fail("could not repair JSON payload", text, candidate, default)


def extract_json(text: str) -> Optional[str]:
    """Return the sanitized JSON payload found in *text*, or ``None``.

    The returned string is the best candidate for parsing. It may still need
    :func:`repair_json` or :func:`close_truncated_json` if the model produced
    invalid or truncated JSON; :func:`loads` does all of that for you.
    """
    raw = _extract_raw(text)
    return sanitize_json_string(raw) if raw is not None else None


def find_complete_json(text: str) -> Optional[str]:
    """Return the balanced JSON object or array at the start of *text*.

    Braces and brackets inside string literals are ignored. Returns ``None``
    if *text* does not start with ``{`` or ``[`` or is never closed.
    """
    if not text or text[0] not in "{[":
        return None
    end = _match_brackets(text, 0)
    return text[: end + 1] if end is not None else None


def strip_control_characters(text: str) -> str:
    """Remove ASCII control characters except tab, newline and carriage return."""
    if not text:
        return text
    return "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")


def heal_json_string(json_str: str) -> str:
    """Fix structural mistakes that do not need a decode error to locate.

    * literal newlines, tabs, carriage returns and other control characters
      inside string values are escaped;
    * trailing commas before ``}`` or ``]`` are removed.
    """
    if not json_str:
        return json_str

    out: List[str] = []
    in_string = False
    escaped = False
    n = len(json_str)
    i = 0
    while i < n:
        ch = json_str[i]
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ch < " ":
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
                out.append(ch)
            elif ch == ",":
                j = i + 1
                while j < n and json_str[j] in " \t\n\r":
                    j += 1
                if j < n and json_str[j] in "]}":
                    i += 1  # drop the trailing comma
                    continue
                out.append(ch)
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def sanitize_json_string(json_str: str) -> str:
    r"""Make a JSON candidate safe to hand to :func:`json.loads`.

    Applies, in order: :func:`strip_control_characters`,
    :func:`heal_json_string`, then doubles any backslash that does not start a
    valid JSON escape (``\"  \\  \/  \b  \f  \n  \r  \t  \uXXXX``), so that
    regexes and Windows paths written by the model survive parsing.
    """
    if not json_str:
        return json_str

    s = heal_json_string(strip_control_characters(json_str))

    out: List[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in _VALID_ESCAPES:
                out.append(s[i : i + 2])
                i += 2
                continue
            hex_digits = s[i + 2 : i + 6]
            if nxt == "u" and len(hex_digits) == 4 and all(c in _HEX for c in hex_digits):
                out.append(s[i : i + 6])
                i += 6
                continue
            out.append("\\\\")
            i += 1
            continue
        if ch == "\\":  # lone trailing backslash
            out.append("\\\\")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def repair_json(json_str: str, error: json.JSONDecodeError) -> str:
    """Apply one targeted fix for the decode *error* raised on *json_str*.

    Handles missing commas, missing colons, unquoted property names, unescaped
    double quotes inside strings, and trailing non-JSON data. Returns
    *json_str* unchanged when no fix applies, so callers can loop until the
    string stops changing.
    """
    if not json_str or error.pos is None:
        return json_str

    pos = min(error.pos, len(json_str))
    msg = error.msg
    nxt = json_str[pos] if pos < len(json_str) else ""

    try:
        if msg.startswith("Extra data"):
            return json_str[:pos].rstrip()

        if "':' delimiter" in msg:
            if nxt == '"':
                return json_str[:pos] + ":" + json_str[pos:]
            return json_str

        if "',' delimiter" in msg:
            if nxt and nxt in '"{[-0123456789tfn':
                # "a": 1 "b": 2   ->   "a": 1, "b": 2
                return json_str[:pos] + "," + json_str[pos:]
            if nxt:
                # The previous string ended early because of an unescaped
                # quote: "he said "hi" ok" -> escape that quote.
                q = json_str.rfind('"', 0, pos)
                if q > 0:
                    return json_str[:q] + '\\"' + json_str[q + 1 :]
            return json_str

        if msg.startswith("Expecting property name"):
            # {key: 1}  ->  {"key": 1}
            m = _IDENT_RE.match(json_str, pos)
            if m:
                after = json_str[m.end() :].lstrip()
                if after.startswith(":"):
                    return json_str[:pos] + '"' + m.group(0) + '"' + json_str[m.end() :]
            return json_str
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("repair_json failed: %s", exc)

    return json_str


def close_truncated_json(json_str: str) -> Optional[str]:
    """Close a JSON document that was cut off, e.g. by a token limit.

    Unterminated strings are closed, dangling ``,`` and ``:`` are dealt with,
    and missing ``]``/``}`` are appended. If the last member is too incomplete
    to keep, it is dropped. Returns a string that :func:`json.loads` accepts,
    or ``None`` if the document cannot be closed.
    """
    if not json_str:
        return None

    s = json_str.rstrip()
    stack, in_string, _, _ = _scan_state(s)
    if not stack and not in_string:
        # Not truncated: nothing to close.
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError:
            return None

    for _ in range(64):  # each round drops one incomplete trailing member
        stack, in_string, escaped, commas = _scan_state(s)
        base = s
        if in_string:
            if escaped:
                base = base[:-1]
            base += '"'
        closers = "".join("}" if c == "{" else "]" for c in reversed(stack))

        attempts = [base + closers]
        stripped = base.rstrip()
        if stripped.endswith(","):
            attempts.append(stripped[:-1] + closers)
        if stripped.endswith(":"):
            attempts.append(stripped + " null" + closers)
        for attempt in attempts:
            try:
                json.loads(attempt)
                return attempt
            except json.JSONDecodeError:
                continue

        # Drop the incomplete trailing member: cut back to the last comma, or
        # empty the innermost open container if it has no complete member.
        last_comma = commas[-1] if commas else -1
        opener = _innermost_unclosed_opener(s)
        if opener is not None and opener > last_comma:
            new_s = s[: opener + 1]
        elif last_comma >= 0:
            new_s = s[:last_comma].rstrip()
        else:
            return None
        if new_s == s:
            return None
        s = new_s
    return None


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _fail(message: str, text: str, candidate: Optional[str], default: Any) -> Any:
    if default is not _MISSING:
        return default
    raise JSONHealError(message, text, candidate)


def _parse_with_repairs(candidate: str, max_repairs: int) -> Tuple[bool, Any]:
    s = candidate
    for _ in range(max(0, max_repairs) + 1):
        try:
            return True, json.loads(s)
        except json.JSONDecodeError as exc:
            fixed = repair_json(s, exc)
            if fixed == s:
                break
            logger.debug("Repaired %r at position %s", exc.msg, exc.pos)
            s = fixed

    closed = close_truncated_json(s)
    if closed is None and s != candidate:
        closed = close_truncated_json(candidate)
    if closed is not None:
        try:
            return True, json.loads(closed)
        except json.JSONDecodeError:  # pragma: no cover - close_truncated validates
            pass
    return False, None


def _try_literal_eval(raw: str) -> Tuple[bool, Any]:
    try:
        value = ast.literal_eval(raw.strip())
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False, None
    if isinstance(value, (dict, list)):
        return True, value
    return False, None


def _is_opener(text: str, i: int) -> bool:
    ch = text[i]
    if ch == "{":
        return True
    return ch == "[" and _ARRAY_START_RE.match(text, i) is not None


def _next_opener(text: str, start: int) -> Optional[int]:
    i = start
    n = len(text)
    while i < n:
        j_obj = text.find("{", i)
        j_arr = text.find("[", i)
        candidates = [j for j in (j_obj, j_arr) if j != -1]
        if not candidates:
            return None
        j = min(candidates)
        if _is_opener(text, j):
            return j
        i = j + 1
    return None


def _match_brackets(text: str, start: int) -> Optional[int]:
    """Index of the bracket closing the one at *start*, ignoring strings."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i
    return None


def _scan_state(s: str) -> Tuple[List[str], bool, bool, List[int]]:
    """Return (open bracket stack, in_string, escaped, indexes of commas outside strings)."""
    stack: List[str] = []
    commas: List[int] = []
    in_string = False
    escaped = False
    for i, ch in enumerate(s):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == "," and stack:
            commas.append(i)
    return stack, in_string, escaped, commas


def _innermost_unclosed_opener(s: str) -> Optional[int]:
    stack: List[int] = []
    in_string = False
    escaped = False
    for i, ch in enumerate(s):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(i)
        elif ch in "}]" and stack:
            stack.pop()
    return stack[-1] if stack else None


def _parses(candidate: str) -> Tuple[bool, Any]:
    try:
        return True, json.loads(sanitize_json_string(candidate))
    except json.JSONDecodeError:
        return False, None


def _extract_raw(text: str) -> Optional[str]:
    """Locate the JSON payload in *text* without modifying it."""
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    # 1. The whole reply is JSON.
    ok, _ = _parses(stripped)
    if ok and stripped[0] in "{[":
        return stripped

    # 2. Fenced code blocks, in order.
    truncated_fence: Optional[str] = None
    for block in _FENCE_RE.findall(text):
        found, truncated = _scan_candidates(block)
        if found is not None:
            return found
        if truncated is not None and truncated_fence is None:
            truncated_fence = truncated

    # 3. Free text.
    found, truncated = _scan_candidates(text)
    if found is not None:
        return found
    if truncated_fence is not None:
        return truncated_fence
    return truncated


def _scan_candidates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (best balanced candidate, truncated tail) found in *text*.

    Only top-level candidates are considered: once a balanced object is found,
    scanning resumes after it, so an inner object is never returned in place
    of the broken outer one that contains it.
    """
    balanced: List[str] = []
    truncated: Optional[str] = None

    i = _next_opener(text, 0)
    while i is not None:
        end = _match_brackets(text, i)
        if end is None:
            truncated = text[i:].rstrip()
            if truncated.endswith("```"):
                truncated = truncated[:-3].rstrip()
            break
        balanced.append(text[i : end + 1])
        i = _next_opener(text, end + 1)

    for cand in balanced:
        ok, value = _parses(cand)
        if ok and (isinstance(value, dict) or (isinstance(value, list) and _is_structured_list(value))):
            return cand, truncated

    # Nothing parses as-is: hand back the first object that looks like JSON so
    # the caller can repair it.
    for cand in balanced:
        if cand[0] == "{" and (":" in cand or '"' in cand):
            return cand, truncated
    for cand in balanced:
        if cand[0] == "[" and len(cand) > 2:
            return cand, truncated
    return None, truncated


def _is_structured_list(value: list) -> bool:
    return any(isinstance(v, (dict, list)) for v in value) or (len(value) > 0 and all(isinstance(v, str) for v in value))
