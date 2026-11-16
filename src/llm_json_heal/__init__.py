"""llm-json-heal: recover JSON from messy LLM output.

>>> from llm_json_heal import loads
>>> loads('Sure! ```json\\n{"verdict": "ok", "score": 0.9,}\\n```')
{'verdict': 'ok', 'score': 0.9}
"""

from .core import (
    JSONHealError,
    close_truncated_json,
    extract_json,
    find_complete_json,
    heal_json_string,
    loads,
    repair_json,
    sanitize_json_string,
    strip_control_characters,
)
from .normalize import clamp, ensure_list, pick, to_percentage

__version__ = "0.1.0"

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
    "ensure_list",
    "clamp",
    "to_percentage",
    "pick",
    "__version__",
]
