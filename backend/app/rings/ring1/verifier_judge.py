"""Ring 1 - independent re-derivation by a second, cheaper model.

The pattern is usually called "LLM as judge". The important detail is that the
judge does not grade the first answer; it is asked to work the problem out
again from the source material, and only then to say whether the two agree.
Asking a model "is this answer good?" gets you agreement. Asking it "what is
the answer?" and comparing gets you a signal.

It runs on a small model on purpose. Ring 1 touches a single-digit percentage
of traffic and has to stay inside a spend cap, so the verifier is priced like
a rounding error, not like a second production model.

Structured output is enforced: the judge must return JSON, we parse it, and a
parse failure gets exactly one stricter retry before we give up and record the
check as unavailable rather than inventing a verdict.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("controlplane.ring1.judge")

SYSTEM = (
    "You are a verifier for an enterprise AI control plane. You do not chat. "
    "You independently re-derive the answer to a question from the supplied "
    "source material, compare your own conclusion to a candidate answer, and "
    "report the comparison. Return only a single JSON object and no other text."
)

TEMPLATE = """Question that was asked:
{prompt}

Source material available (this is the only ground truth; if it is empty, say so):
{context}

Candidate answer produced by the production model:
{response}

Work the question out yourself from the source material, then compare.
Return only this JSON object:
{{"agrees": true or false,
  "judge_reasoning": "one or two sentences saying what you derived and where it differs",
  "confidence": 0.0 to 1.0,
  "corrected_claim": "the corrected statement, or an empty string if you agree"}}"""

STRICT_RETRY = "\n\nYour previous reply was not valid JSON. Reply with the JSON object only."


def _parse(raw: str) -> dict | None:
    blob = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", blob, re.S)
    if fence:
        blob = fence.group(1)
    start, end = blob.find("{"), blob.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(blob[start: end + 1])
    except Exception:
        return None
    if not isinstance(data, dict) or "agrees" not in data:
        return None
    return {
        "agrees": bool(data.get("agrees")),
        "judge_reasoning": str(data.get("judge_reasoning", ""))[:800],
        "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.5) or 0.5))),
        "corrected_claim": str(data.get("corrected_claim", ""))[:500],
    }


async def judge_verify(
    prompt: str,
    response_text: str,
    context_docs: list[str] | None,
    provider: str = "mock",
    model: str = "controlplane-sim-judge",
    generate=None,
) -> dict:
    """Returns {ran, agrees, judge_reasoning, confidence, corrected_claim, cost_usd}."""
    context = "\n\n---\n\n".join(context_docs or []) or "(no source material was supplied)"
    body = TEMPLATE.format(prompt=prompt, context=context[:6000], response=response_text[:4000])

    if generate is None:
        from app.core.llm_gateway import LLMGateway

        gw = LLMGateway()

        async def generate(text, system=SYSTEM):  # noqa: ANN001
            res = await gw.complete(text, provider, model, system=system, max_tokens=400)
            return res.text, res.cost_usd, res.tokens_in + res.tokens_out
    else:
        _inner = generate

        async def generate(text, system=SYSTEM):  # noqa: ANN001
            return (await _inner(text)), 0.0, 0

    total_cost, total_tokens = 0.0, 0
    for attempt, suffix in enumerate(("", STRICT_RETRY)):
        try:
            raw, cost, tokens = await generate(body + suffix)
        except Exception as exc:
            log.warning("judge call failed: %s", exc)
            return {
                "ran": False,
                "error": f"{type(exc).__name__}: {exc}",
                "agrees": None,
                "confidence": 0.0,
                "judge_reasoning": "The verifier model was unreachable; this check is unavailable.",
                "cost_usd": round(total_cost, 6),
            }
        total_cost += cost
        total_tokens += tokens
        parsed = _parse(raw)
        if parsed is not None:
            parsed.update({
                "ran": True,
                "attempts": attempt + 1,
                "model": f"{provider}/{model}",
                "cost_usd": round(total_cost, 6),
                "tokens": total_tokens,
            })
            return parsed

    return {
        "ran": False,
        "error": "verifier did not return parseable JSON after a retry",
        "agrees": None,
        "confidence": 0.0,
        "judge_reasoning": (
            "The verifier could not be parsed. Recorded as unavailable rather than "
            "guessed; the response stays flagged for a human."
        ),
        "cost_usd": round(total_cost, 6),
        "tokens": total_tokens,
    }
