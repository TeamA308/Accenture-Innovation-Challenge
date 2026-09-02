"""Ring 0 - how sure was the model, really?

The pitch line is "fluency is not accuracy, and the model offers no honest
signal of its own doubt". That is only half true: for providers that return
token log probabilities, the doubt is right there in the response, unused.

Primary estimator (used whenever logprobs are available)
-------------------------------------------------------
Normalised token entropy. For each generated token we have log p(token). The
mean negative log-likelihood is the model's own surprise at its own output.
We map it onto 0..1 where 0 = certain, 1 = maximally unsure, and we also report
the worst-scoring tokens so the evidence drawer can point at *which words* the
model was unsure about. That is far more useful to a reviewer than a scalar.

Fallback estimator (Anthropic and most hosted APIs expose no logprobs)
---------------------------------------------------------------------
A lexical proxy: density of hedging language, density of unsourced numbers,
and use of attribution-dodging phrases ("as far as I can tell"). This is
weaker and we say so explicitly -- the returned dict always names the method
used, and the UI shows it, because a governance tool that hides how it
measured something is not a governance tool.
"""
from __future__ import annotations

import math
import re

HEDGE_TERMS = {
    "might", "maybe", "perhaps", "possibly", "probably", "generally",
    "typically", "usually", "often", "believe", "think", "appears",
    "seems", "roughly", "approximately", "around", "about", "likely",
    "unclear", "unsure", "assume", "presumably", "could", "should",
    "somewhere", "estimate", "estimated", "potentially", "arguably",
}

HEDGE_PHRASES = (
    "as far as i can tell", "i believe", "i think", "if i recall",
    "to my knowledge", "i am not certain", "i'm not certain", "i cannot confirm",
    "it is likely that", "my understanding is", "i would guess", "not entirely sure",
)

# The opposite signal: absolute language. High certainty plus low evidence is
# the "confidently wrong" quadrant, which is worse than visible hedging.
CERTAINTY_PHRASES = (
    "absolutely", "definitely", "certainly", "without a doubt", "guaranteed",
    "always", "never", "every", "all cases", "no exceptions", "in full",
)

_WORD = re.compile(r"[A-Za-z']+")
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def estimate_uncertainty(
    response_text: str,
    token_logprobs: list[float] | None = None,
    tokens: list[str] | None = None,
) -> dict:
    words = _WORD.findall(response_text.lower())
    n_words = max(1, len(words))

    hedge_hits = [w for w in words if w in HEDGE_TERMS]
    low = response_text.lower()
    phrase_hits = [p for p in HEDGE_PHRASES if p in low]
    certainty_hits = [p for p in CERTAINTY_PHRASES if p in low]
    number_density = len(_NUMBER.findall(response_text)) / n_words

    if token_logprobs:
        # Mean negative log-likelihood -> perplexity -> 0..1.
        mean_nll = -sum(token_logprobs) / len(token_logprobs)
        perplexity = math.exp(mean_nll)
        # A perplexity of 1.0 is total certainty; ~6 is very unsure for a
        # well-behaved assistant. log-scale the mapping.
        score = _clamp(math.log(max(perplexity, 1.0)) / math.log(6.0))

        toks = tokens or response_text.split()
        pairs = list(zip(toks, token_logprobs))
        worst = sorted(pairs, key=lambda p: p[1])[:6]
        method = "token_logprob_entropy"
        detail = {
            "mean_neg_logprob": round(mean_nll, 4),
            "perplexity": round(perplexity, 3),
            "n_tokens": len(token_logprobs),
            "least_confident_tokens": [
                {"token": t, "logprob": round(lp, 3), "p": round(math.exp(lp), 3)}
                for t, lp in worst
            ],
        }
    else:
        # Lexical fallback. Documented as weaker on purpose.
        hedge_rate = len(hedge_hits) / n_words
        score = _clamp(hedge_rate * 14 + len(phrase_hits) * 0.18 + min(number_density, 0.12) * 1.2)
        method = "lexical_fallback_no_logprobs"
        detail = {
            "note": (
                "This provider does not expose token log probabilities, so the "
                "score is a lexical proxy and is less reliable than the "
                "logprob estimator. Treated as advisory, never as sole grounds "
                "to block."
            ),
            "hedge_rate": round(hedge_rate, 4),
        }

    # Confidently-wrong marker: strongly assertive language with no hedging.
    fluency_confidence = _clamp(
        0.5 + len(certainty_hits) * 0.15 - len(hedge_hits) * 0.06 - len(phrase_hits) * 0.1
    )

    return {
        "score": round(score, 3),
        "method": method,
        "logprobs_available": bool(token_logprobs),
        "hedge_terms": sorted(set(hedge_hits))[:12],
        "hedge_phrases": phrase_hits,
        "certainty_phrases": certainty_hits,
        "assertiveness": round(fluency_confidence, 3),
        "number_density": round(number_density, 4),
        **detail,
    }
