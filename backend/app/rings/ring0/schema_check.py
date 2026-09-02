"""Ring 0 - deterministic structure and arithmetic checks.

Language models are fluent calculators and unreliable ones. When an answer
shows its working, we can simply re-do the sum. This is the cheapest and most
certain signal in the whole system: no model, no threshold, no judgement call.
It is either arithmetic or it is not.

Two checks live here:
  * `check_arithmetic` re-derives every explicit equation in the text.
  * `check_schema` validates the answer against a caller-supplied shape, for
    the case where the model output feeds a machine rather than a person.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

NUM = r"(?:\d[\d,]*(?:\.\d+)?)"

_EQUATION = re.compile(
    rf"({NUM})\s*([+\-*x×/÷])\s*({NUM})\s*=\s*({NUM})", re.I
)
_PERCENT_OF = re.compile(
    rf"({NUM})\s*(?:percent|%)\s*of\s*({NUM})\s*(?:is|=|equals)\s*({NUM})", re.I
)
# "145 seats at Rs 8400 per seat = 1218000"
_RATE = re.compile(
    rf"({NUM})\s*\w{{0,12}}\s*at\s*(?:Rs\.?|INR|\$|£|€)?\s*({NUM})\s*(?:per|each|/)\s*\w{{0,12}}"
    rf"[^\n=]{{0,40}}=\s*(?:Rs\.?|INR|\$|£|€)?\s*({NUM})",
    re.I,
)

_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "x": lambda a, b: a * b,
    "×": lambda a, b: a * b,
    "/": lambda a, b: a / b if b else float("nan"),
    "÷": lambda a, b: a / b if b else float("nan"),
}

# Money answers are routinely rounded. Anything inside 0.5% (or 1 unit) passes.
REL_TOLERANCE = 0.005
ABS_TOLERANCE = 1.0


def _f(token: str) -> float:
    return float(token.replace(",", ""))


def _fmt(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".") if v % 1 else f"{int(v):,}"


def _close(claimed: float, actual: float) -> bool:
    if claimed == actual:
        return True
    denom = max(abs(actual), 1e-9)
    return abs(claimed - actual) <= max(ABS_TOLERANCE, denom * REL_TOLERANCE)


@dataclass
class ArithmeticFinding:
    expression: str
    claimed: float
    actual: float
    correct: bool
    start: int
    end: int

    def to_dict(self) -> dict:
        return {
            "expression": self.expression,
            "claimed": self.claimed,
            "actual": round(self.actual, 4),
            "correct": self.correct,
            "start": self.start,
            "end": self.end,
            "message": (
                ""
                if self.correct
                else f"{self.expression} -- stated {_fmt(self.claimed)}, "
                     f"recomputed {_fmt(self.actual)} "
                     f"(off by {_fmt(abs(self.claimed - self.actual))})"
            ),
        }


def check_arithmetic(text: str) -> list[dict]:
    findings: list[ArithmeticFinding] = []
    seen: set[tuple[int, int]] = set()

    # Rate expressions first: they subsume the bare equation inside them.
    for m in _RATE.finditer(text):
        qty, rate, claimed = _f(m.group(1)), _f(m.group(2)), _f(m.group(3))
        actual = qty * rate
        findings.append(
            ArithmeticFinding(m.group(0).strip(), claimed, actual, _close(claimed, actual),
                              m.start(), m.end())
        )
        seen.add((m.start(), m.end()))

    for m in _EQUATION.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in seen):
            continue  # already covered by a wider rate expression
        a, op, b, claimed = _f(m.group(1)), m.group(2), _f(m.group(3)), _f(m.group(4))
        actual = _OPS[op.lower()](a, b)
        findings.append(
            ArithmeticFinding(m.group(0).strip(), claimed, actual, _close(claimed, actual),
                              m.start(), m.end())
        )
        seen.add((m.start(), m.end()))

    for m in _PERCENT_OF.finditer(text):
        if (m.start(), m.end()) in seen:
            continue
        pct, base, claimed = _f(m.group(1)), _f(m.group(2)), _f(m.group(3))
        actual = base * pct / 100
        findings.append(
            ArithmeticFinding(m.group(0).strip(), claimed, actual, _close(claimed, actual),
                              m.start(), m.end())
        )


    return [f.to_dict() for f in sorted(findings, key=lambda f: f.start)]


_TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


def check_schema(text: str, expected_schema: dict | None) -> dict:
    """Validate that the answer is JSON of the expected shape.

    `expected_schema` is a flat mapping of field name -> type name, e.g.
    {"decision": "string", "limit": "number"}. Deliberately simple: this
    guards machine-to-machine output, and a small honest check beats a large
    dependency.
    """
    if not expected_schema:
        return {"applicable": False, "valid": True, "errors": []}

    blob = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", blob, re.S)
    if fence:
        blob = fence.group(1).strip()
    else:
        first, last = blob.find("{"), blob.rfind("}")
        if first != -1 and last > first:
            blob = blob[first: last + 1]

    try:
        parsed = json.loads(blob)
    except Exception as exc:
        return {"applicable": True, "valid": False,
                "errors": [f"response is not valid JSON: {exc}"]}

    if not isinstance(parsed, dict):
        return {"applicable": True, "valid": False,
                "errors": ["top-level JSON value is not an object"]}

    errors: list[str] = []
    for field, type_name in expected_schema.items():
        if field not in parsed:
            errors.append(f"missing required field '{field}'")
            continue
        expected = _TYPES.get(str(type_name).lower())
        if expected and not isinstance(parsed[field], expected):
            errors.append(
                f"field '{field}' should be {type_name}, got "
                f"{type(parsed[field]).__name__}"
            )
    return {"applicable": True, "valid": not errors, "errors": errors, "parsed": parsed}


def check_arithmetic_and_schema(text: str, expected_schema: dict | None = None) -> dict:
    arithmetic = check_arithmetic(text)
    schema = check_schema(text, expected_schema)
    bad = [a for a in arithmetic if not a["correct"]]
    errors = [a["message"] for a in bad] + schema["errors"]
    return {
        "valid": not errors,
        "errors": errors,
        "arithmetic": arithmetic,
        "arithmetic_checked": len(arithmetic),
        "arithmetic_failed": len(bad),
        "schema": schema,
    }
