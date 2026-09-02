"""Ring 0 - is each claim traceable to a source document?

What "grounding" means here
---------------------------
Take the answer apart into individual factual claims, then try to find each one
in the documents the answer was supposed to be based on. Claims we can find get
a citation. Claims we cannot get flagged. Claims that *contradict* the source
get flagged hardest, because that is a hallucination with evidence attached.

Why not a neural entailment model
---------------------------------
Vectara's HHEM and similar cross-encoders are the standard answer, and we
support one as an optional backend. They are not the default because of what
this system is for. A reviewer or an auditor needs to be told *why* an answer
was flagged. "Entailment probability 0.31" is not a reason. "The answer says
the refund window is 45 days; the source policy says 30 days, section 2" is a
reason -- it survives being read out loud in a compliance meeting.

So the default engine is deterministic and citable:
  * inverse-document-frequency weighted overlap finds the supporting sentence,
  * then every number, quantity and money amount in the claim is checked
    against that sentence specifically.

Numbers are where enterprise hallucination actually lives -- a wrong refund
window, a wrong SLA credit, a wrong limit -- and they are exactly the part a
similarity score smooths over.
"""
from __future__ import annotations

import math
import re
from collections import Counter

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "of", "to", "in", "on", "for", "with", "and", "or", "but", "if", "then",
    "that", "this", "these", "those", "it", "its", "as", "at", "by", "from",
    "we", "you", "i", "they", "he", "she", "our", "your", "their", "his", "her",
    "will", "would", "can", "could", "should", "may", "might", "must", "do",
    "does", "did", "not", "no", "so", "than", "there", "here", "also", "any",
    "all", "each", "which", "what", "when", "where", "who", "how", "have",
    "has", "had", "up", "out", "about", "into", "over", "after", "before",
}

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n{2,}|\n(?=[-*•])")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")

_UNIT = (
    r"percent|%|days?|business days?|hours?|minutes?|months?|years?|weeks?|"
    r"seats?|users?|licen[cs]es?|lakh|crore|times?"
)
_QUANTITY = re.compile(rf"\b(\d[\d,]*(?:\.\d+)?)\s*({_UNIT})\b", re.I)
_MONEY = re.compile(r"(?:Rs\.?|INR|₹|\$|£|€|USD)\s*(\d[\d,]*(?:\.\d+)?)", re.I)
_BARE_NUMBER = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\b")

# Sentences that state an opinion or an intention are not checkable facts. We
# label them rather than silently scoring them, so coverage is not diluted by
# "I would send this today".
_NON_FACTUAL = re.compile(
    r"^\s*(?:i (?:would|will|can|recommend|suggest|think|believe)|"
    r"let me|please|thanks|thank you|happy to|feel free|"
    r"in short|to summarise|to summarize|here (?:is|are))",
    re.I,
)


def _norm_num(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return float("nan")


def _sentences(text: str) -> list[str]:
    # A markdown heading is its own unit. Without this a heading fuses to the
    # sentence below it and the citation we show a reviewer looks mangled.
    text = re.sub(r"\n(#{1,6}[^\n]*)\n", r"\n\n\1\n\n", text or "")
    parts = [p.strip(" -*•\t#") for p in _SENT_SPLIT.split(text) if p and p.strip()]
    out: list[str] = []
    for p in parts:
        # Split long compound sentences so a half-true sentence does not get
        # full credit for its true half.
        if len(p) > 220:
            out.extend(x.strip() for x in re.split(r";\s+|,\s+(?=and\s|though\s|but\s)", p) if x.strip())
        else:
            out.append(p)
    return [s for s in out if len(s) > 12]


def _content_tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)
            if w.lower() not in STOPWORDS and len(w) > 2]


def _quantities(text: str) -> list[tuple[str, str]]:
    """Return (value, unit) pairs. Money is unit 'currency'; bare numbers ''."""
    out: list[tuple[str, str]] = []
    claimed: list[tuple[int, int]] = []
    for m in _QUANTITY.finditer(text):
        out.append((m.group(1).replace(",", ""), m.group(2).lower().rstrip("s")))
        claimed.append(m.span())
    for m in _MONEY.finditer(text):
        if any(s <= m.start(1) < e for s, e in claimed):
            continue
        out.append((m.group(1).replace(",", ""), "currency"))
        claimed.append(m.span())
    for m in _BARE_NUMBER.finditer(text):
        if any(s <= m.start() < e for s, e in claimed):
            continue
        val = m.group(1).replace(",", "")
        if len(val.replace(".", "")) >= 2:  # ignore "a 2 hour window" noise? keep >=2 digits
            out.append((val, ""))
    return out


class _Index:
    """Tiny IDF index over the supplied source documents."""

    def __init__(self, docs: list[str]) -> None:
        self.docs = docs
        self.units: list[dict] = []
        for di, doc in enumerate(docs):
            for sent in _sentences(doc):
                self.units.append({
                    "doc_index": di,
                    "text": sent,
                    "tokens": set(_content_tokens(sent)),
                    "quantities": _quantities(sent),
                })
        n = max(1, len(self.units))
        df = Counter()
        for u in self.units:
            df.update(u["tokens"])
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.default_idf = math.log(n + 1)
        self.all_numbers = {q[0] for u in self.units for q in u["quantities"]}

    def best_match(self, claim_tokens: list[str]) -> tuple[dict | None, float]:
        if not self.units or not claim_tokens:
            return None, 0.0
        weights = {t: self.idf.get(t, self.default_idf) for t in set(claim_tokens)}
        total = sum(weights.values()) or 1.0
        best, best_score = None, 0.0
        for u in self.units:
            hit = sum(w for t, w in weights.items() if t in u["tokens"])
            score = hit / total
            if score > best_score:
                best, best_score = u, score
        return best, best_score

    def find_rival(self, claim_tokens: set[str], value: str, unit: str) -> dict | None:
        """Look for a source sentence that states a *different* value for the
        same unit while talking about the same subject.

        This is the difference between "we could not find this figure" and
        "the source says something else", and only the second one is a
        hallucination we can prove. We require at least two shared content
        words so we do not compare a refund window against a warranty period.
        """
        if not unit:
            return None
        best, best_shared = None, 1
        for u in self.units:
            shared = len(claim_tokens & u["tokens"])
            if shared < 2 or shared <= best_shared:
                continue
            rivals = [v for v, un in u["quantities"] if un == unit and v != value]
            if rivals:
                best, best_shared = {"value": rivals[0], "sentence": u["text"],
                                     "doc_index": u["doc_index"]}, shared
        return best


def check_grounding(response_text: str, context_docs: list[str] | None) -> dict:
    """Claim-level grounding with citations.

    Returns a dict whose `score` is 0..1 coverage, or None when there is
    nothing to check against. `status` is one of grounded / partial /
    ungrounded / ungroundable.
    """
    docs = [d for d in (context_docs or []) if d and d.strip()]
    if not docs:
        # There is often no reliable ground truth. Saying "ungroundable" is the
        # honest answer; scoring it 1.0 would be a lie that passes.
        return {
            "score": None,
            "status": "ungroundable",
            "claims": [],
            "supported": 0,
            "unsupported": 0,
            "contradicted": 0,
            "note": (
                "No source documents were supplied with this request, so no "
                "claim in the answer can be verified. This is reported, never "
                "silently treated as grounded."
            ),
        }

    index = _Index(docs)
    claims = _sentences(response_text)
    results: list[dict] = []

    for claim in claims:
        tokens = _content_tokens(claim)
        if _NON_FACTUAL.match(claim) or len(tokens) < 4:
            results.append({
                "claim": claim,
                "verifiable": False,
                "status": "not_a_factual_claim",
                "support": None,
                "citation": None,
                "issues": [],
            })
            continue

        best, lexical = index.best_match(tokens)
        issues: list[str] = []

        claim_q = _quantities(claim)
        numeric_support = 1.0
        if claim_q:
            src_numbers = set(index.all_numbers)
            near_numbers = {q[0] for q in (best["quantities"] if best else [])}
            token_set = set(tokens)
            found = 0.0
            for value, unit in claim_q:
                if value in near_numbers:
                    found += 1
                elif value in src_numbers:
                    found += 0.6  # present in the corpus, but not in this passage
                else:
                    rival = index.find_rival(token_set, value, unit)
                    if rival:
                        issues.append(
                            f"contradicts source: answer says {value} {unit}, "
                            f"source says {rival['value']} {unit} "
                            f"(\"{rival['sentence'][:120]}\")"
                        )
                    else:
                        issues.append(
                            f"figure '{value}{(' ' + unit) if unit else ''}' does not "
                            "appear in any source document"
                        )
            numeric_support = found / len(claim_q)

        support = round(0.45 * lexical + 0.55 * numeric_support, 3) if claim_q \
            else round(lexical, 3)

        if any("contradicts source" in i for i in issues):
            status = "contradicted"
        elif support >= 0.62 and not issues:
            status = "supported"
        elif support >= 0.35:
            status = "partial"
        else:
            status = "unsupported"

        results.append({
            "claim": claim,
            "verifiable": True,
            "status": status,
            "support": support,
            "lexical_overlap": round(lexical, 3),
            "numeric_support": round(numeric_support, 3),
            "citation": (
                {"doc_index": best["doc_index"], "text": best["text"]}
                if best and lexical > 0.12 else None
            ),
            "issues": issues,
        })

    verifiable = [r for r in results if r["verifiable"]]
    if not verifiable:
        return {
            "score": None,
            "status": "ungroundable",
            "claims": results,
            "supported": 0,
            "unsupported": 0,
            "contradicted": 0,
            "note": "The answer contains no checkable factual claim.",
        }

    contradicted = sum(1 for r in verifiable if r["status"] == "contradicted")
    supported = sum(1 for r in verifiable if r["status"] == "supported")
    unsupported = sum(1 for r in verifiable if r["status"] in ("unsupported", "contradicted"))
    score = sum(r["support"] for r in verifiable) / len(verifiable)
    # A single contradicted claim should sink the score; it is worse than a gap.
    score = max(0.0, score - 0.25 * contradicted)

    if contradicted:
        status = "contradicted"
    elif score >= 0.75:
        status = "grounded"
    elif score >= 0.45:
        status = "partial"
    else:
        status = "ungrounded"

    return {
        "score": round(score, 3),
        "status": status,
        "claims": results,
        "n_claims": len(verifiable),
        "supported": supported,
        "unsupported": unsupported,
        "contradicted": contradicted,
        "engine": "deterministic_evidence_match",
        "note": "",
    }


def unsupported_claims(result: dict) -> list[str]:
    return [c["claim"] for c in result.get("claims", [])
            if c.get("status") in ("unsupported", "contradicted")]
