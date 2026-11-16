"""Coerce loosely typed LLM field values into predictable Python types.

Models return ``"0.85"``, ``0.85`` and ``85`` for the same confidence, a
comma-separated string where a list was asked for, and ``"High risk"`` where
``"HIGH"`` was expected. These helpers normalize such values after
:func:`llm_json_heal.loads` has produced a dict.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, TypeVar

__all__ = ["ensure_list", "clamp", "to_percentage", "pick"]

T = TypeVar("T")


def ensure_list(value: Any, *, split: str = ",") -> List[Any]:
    """Return *value* as a list.

    * lists are returned unchanged and tuples/sets are converted;
    * ``None`` and blank strings become ``[]``;
    * strings are split on *split* (pass ``split=""`` to disable) and stripped;
    * anything else is wrapped: ``5`` -> ``[5]``.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    if isinstance(value, str):
        if not value.strip():
            return []
        if split and split in value:
            return [part.strip() for part in value.split(split) if part.strip()]
        return [value.strip()]
    return [value]


def clamp(value: Any, low: float, high: float, *, default: Optional[float] = None, ndigits: Optional[int] = None) -> Optional[float]:
    """Convert *value* to a float within ``[low, high]``.

    Strings such as ``"7.5"`` or ``"7.5/10"`` are accepted (the first number is
    used). Returns *default* if no number can be read. If *ndigits* is given
    the result is rounded.
    """
    number = _to_float(value)
    if number is None:
        return default
    number = max(low, min(high, number))
    return round(number, ndigits) if ndigits is not None else number


def to_percentage(value: Any, *, default: int = 0) -> int:
    """Convert a confidence-style value to an integer from 0 to 100.

    Values greater than 0 and at most 1 are treated as fractions
    (``0.85`` -> ``85``, ``1`` -> ``100``); larger values are treated as
    percentages (``85`` -> ``85``, ``"92%"`` -> ``92``) and clamped.
    """
    number = _to_float(value)
    if number is None:
        return default
    if 0 < number <= 1:
        number *= 100
    return int(max(0, min(100, round(number))))


def pick(value: Any, choices: Iterable[T], *, default: Optional[T] = None, case_sensitive: bool = False) -> Optional[T]:
    """Map *value* onto one of *choices*.

    An exact match wins; otherwise the first choice that appears as a whole
    word inside *value* is returned (``"High risk"`` -> ``"HIGH"`` with
    choices ``["CRITICAL", "HIGH", "MEDIUM", "LOW"]``). Returns *default* when
    nothing matches.
    """
    if value is None:
        return default
    text = str(value).strip()
    options = list(choices)
    flags = 0 if case_sensitive else re.IGNORECASE

    for choice in options:
        if (str(choice) == text) if case_sensitive else (str(choice).lower() == text.lower()):
            return choice
    for choice in options:
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(str(choice)) + r"(?![A-Za-z0-9])", text, flags):
            return choice
    return default


_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _NUMBER_RE.search(str(value))
        if not match:
            return None
        number = float(match.group(0))
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number
