"""Ring 1 - deep faithfulness check against the source documents.

Ring 0 already did a fast, whole-sentence pass. This is the slower version of
the same idea, and it differs in two ways that matter:

  1. Claims are decomposed properly. Ring 0 treats a sentence as a claim.
     Here, a compound sentence like "the window is 30 days and it excludes
     clearance items" becomes two claims, so one true half can no longer carry
     a false half through the check. (This is the atomic-claim decomposition
     that RAGAS' faithfulness metric popularised.)

  2. Every unsupported claim comes back with the passage we compared it
     against, so the review queue shows a reviewer the source text, not a
     score.

The verdict Ring 1 produces can escalate a Ring 0 "allow" -- that is the whole
point of a second look -- but it never silently downgrades a block.
"""
from __future__ import annotations

import re

from app.rings.ring0.grounding import _sentences, check_grounding

# Conjunctions that usually join two independently checkable statements.
_SPLIT = re.compile(
    r"\s*(?:,\s*(?:and|but|though|although|while|whereas)\s+|\s+and\s+(?=(?:it|they|the|there)\b)|;\s*)",
    re.I,
)
_MIN_CLAIM_WORDS = 5


def decompose_claims(text: str) -> list[str]:
    """Break an answer into atomic, individually checkable statements."""
    atoms: list[str] = []
    for sentence in _sentences(text):
        parts = [p.strip(" ,;") for p in _SPLIT.split(sentence) if p and p.strip()]
        if len(parts) <= 1:
            atoms.append(sentence)
            continue
        for part in parts:
            if len(part.split()) >= _MIN_CLAIM_WORDS:
                atoms.append(part)
            elif atoms:
                # Too short to stand alone; keep it attached to its neighbour
                # rather than checking a fragment out of context.
                atoms[-1] = f"{atoms[-1]}, {part}"
            else:
                atoms.append(part)
    return atoms


def faithfulness_check(response_text: str, context_docs: list[str] | None) -> dict:
    """RAGAS-style faithfulness: supported atomic claims / checkable claims."""
    docs = [d for d in (context_docs or []) if d and d.strip()]
    if not docs:
        return {
            "ran": False,
            "faithfulness_score": None,
            "status": "ungroundable",
            "claims": [],
            "unsupported_claims": [],
            "note": (
                "No source documents were supplied, so faithfulness cannot be "
                "measured. Reported as unmeasured, not as passing."
            ),
        }

    atoms = decompose_claims(response_text)
    if not atoms:
        return {
            "ran": True,
            "faithfulness_score": None,
            "status": "no_claims",
            "claims": [],
            "unsupported_claims": [],
            "note": "The answer contained no checkable factual claim.",
        }

    # Reuse the Ring 0 evidence matcher, but at atomic-claim granularity.
    detail = check_grounding("\n".join(f"{a}." for a in atoms), docs)
    claims = detail.get("claims", [])
    checkable = [c for c in claims if c.get("verifiable")]
    supported = [c for c in checkable if c["status"] == "supported"]
    unsupported = [c for c in checkable if c["status"] in ("unsupported", "contradicted")]

    score = (len(supported) + 0.5 * (len(checkable) - len(supported) - len(unsupported))) / len(
        checkable
    ) if checkable else None

    return {
        "ran": True,
        "faithfulness_score": round(score, 3) if score is not None else None,
        "status": detail.get("status"),
        "n_atomic_claims": len(atoms),
        "n_checkable": len(checkable),
        "n_supported": len(supported),
        "n_unsupported": len(unsupported),
        "n_contradicted": sum(1 for c in checkable if c["status"] == "contradicted"),
        "claims": checkable,
        "unsupported_claims": [
            {
                "claim": c["claim"],
                "status": c["status"],
                "issues": c["issues"],
                "closest_source": (c["citation"] or {}).get("text"),
            }
            for c in unsupported
        ],
        "note": "",
    }
