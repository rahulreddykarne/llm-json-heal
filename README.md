# llm-json-heal

[![PyPI](https://img.shields.io/pypi/v/llm-json-heal.svg)](https://pypi.org/project/llm-json-heal/)
[![Python](https://img.shields.io/pypi/pyversions/llm-json-heal.svg)](https://pypi.org/project/llm-json-heal/)
[![CI](https://github.com/rahulreddykarne/llm-json-heal/actions/workflows/ci.yml/badge.svg)](https://github.com/rahulreddykarne/llm-json-heal/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Recover JSON from messy LLM output. Zero dependencies, standard library only.

Language models asked for JSON routinely return something `json.loads` rejects:

- the payload wrapped in prose or a Markdown code fence
- trailing commas, missing commas, missing colons, unquoted keys
- literal newlines and tabs inside strings, stray control characters
- invalid escapes from regexes and Windows paths (`"\d+"`, `"C:\Users"`)
- unescaped double quotes inside a string value
- a reply cut off mid-object by a token limit
- Python-style dicts (`{'a': True, 'b': None}`)

`llm-json-heal` finds the payload, repairs it, and hands you a normal Python object.

## Install

```bash
pip install llm-json-heal
```

## Usage

~~~python
from llm_json_heal import loads

reply = r'''Sure! Here is my analysis:
```json
{
  "verdict": "VULNERABLE",
  "confidence": 0.9,
  "reasoning": "User input reaches $wpdb->query()
without prepare()",
  "patterns": ["\d+", "C:\Users\admin"],
}
```
Let me know if you need more detail.'''

loads(reply)
# {'verdict': 'VULNERABLE', 'confidence': 0.9,
#  'reasoning': 'User input reaches $wpdb->query()\nwithout prepare()',
#  'patterns': ['\\d+', 'C:\\Users\\admin']}
~~~

Truncated replies are closed instead of rejected:

```python
loads('{"verdict": "VULNERABLE", "reasoning": "The input flows into')
# {'verdict': 'VULNERABLE', 'reasoning': 'The input flows into'}
```

When nothing can be recovered, `loads` raises `JSONHealError` (a `ValueError`), or returns a default you supply:

```python
from llm_json_heal import loads, JSONHealError

loads("I could not analyse this file.", default={})   # -> {}

try:
    loads("no json here")
except JSONHealError as exc:
    print(exc, exc.text)
```

## How it works

`loads(text)` runs these steps, stopping as soon as one yields valid JSON:

1. **Locate** the payload: the whole reply, then fenced code blocks, then the first balanced top-level object or array in the prose. Braces inside strings are ignored, and an inner object is never returned in place of the broken outer object that contains it.
2. **Heal**: remove control characters, escape literal newlines and tabs inside strings, drop trailing commas, double backslashes that don't start a valid JSON escape.
3. **Repair**: read the position of each `json.JSONDecodeError` and apply one targeted fix (insert a missing `,` or `:`, quote a bare key, escape a stray quote, drop trailing data). Repeat up to `max_repairs` times.
4. **Close** a truncated document: terminate an open string, resolve a dangling `,` or `:`, drop an incomplete last member, append the missing `]` and `}`.
5. **Fall back** to `ast.literal_eval` for Python-literal syntax.

## Lower-level API

Each step is also exposed on its own:

| Function | Purpose |
|---|---|
| `extract_json(text)` | Return the sanitized payload string, or `None` |
| `find_complete_json(text)` | Return the balanced object or array at the start of `text` |
| `sanitize_json_string(s)` | Control characters + heal + escape fixing |
| `heal_json_string(s)` | Escape literal whitespace in strings, drop trailing commas |
| `strip_control_characters(s)` | Remove ASCII control characters except `\t\n\r` |
| `repair_json(s, error)` | One fix for a given `JSONDecodeError` |
| `close_truncated_json(s)` | Close a cut-off document, or `None` |

## Normalizing field values

Models are also inconsistent about *values*. A few helpers cover the common cases:

```python
from llm_json_heal import to_percentage, clamp, pick, ensure_list

to_percentage("0.85")      # 85
to_percentage("92%")       # 92
clamp("7.53/10", 0, 10, ndigits=1)            # 7.5
pick("High risk", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])  # 'HIGH'
ensure_list("xss, sqli")   # ['xss', 'sqli']
```

## Limitations

- An unescaped quote inside a string is ambiguous. The repair heuristics handle common cases such as `"he said "hi" ok"`, but cannot always recover the intended text.
- Truncation recovery keeps every complete member and drops the incomplete one. It never invents values beyond closing an open string or setting a dangling key to `null`.
- The input is assumed to hold one main payload. If a reply contains several independent JSON objects, the first one that parses is returned.

## Origin

Extracted from the LLM verification stage of the ARKA WordPress plugin vulnerability scanner, where it parsed findings from local (Ollama) and hosted models.

## License

[MIT](LICENSE)
