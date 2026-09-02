"""Ring 0 - credential and secret detection.

A "secret" here is a machine credential: an API key, an access token, a private
key. These leak into model output more often than people expect, usually
because the model was shown a config file or a support ticket that contained
one.

Two layers:
  1. Known-shape patterns. Vendor prefixes are unambiguous, so a match is a
     near-certain hit (an OpenAI key really does start with "sk-").
  2. Shannon entropy. Unknown-vendor secrets have no shape, but they do have a
     signature: a long string with a near-uniform character distribution,
     sitting next to a word like "key", "token" or "password". Ordinary
     English prose never looks like that. This is what catches the credential
     formats that did not exist when we wrote the regexes.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass

from app.rings.ring0.pii import mask


@dataclass
class SecretHit:
    secret_type: str
    start: int
    end: int
    score: float
    text: str
    method: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["text"] = mask(self.text)
        return d


KNOWN_PATTERNS: list[tuple[str, str, float]] = [
    ("OPENAI_KEY", r"\bsk-(?:live-|proj-|test-)?[A-Za-z0-9_\-]{20,}\b", 0.98),
    ("ANTHROPIC_KEY", r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b", 0.98),
    ("AWS_ACCESS_KEY", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b", 0.97),
    ("AWS_SECRET_KEY", r"(?i)aws_secret[^\n]{0,20}[:=]\s*['\"]?([A-Za-z0-9/+=]{40})", 0.95),
    ("GITHUB_TOKEN", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", 0.98),
    ("SLACK_TOKEN", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", 0.97),
    ("GOOGLE_API_KEY", r"\bAIza[0-9A-Za-z_\-]{35}\b", 0.97),
    ("STRIPE_KEY", r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{20,}\b", 0.98),
    ("PRIVATE_KEY_BLOCK", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", 0.99),
    ("JWT", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b", 0.90),
    ("CONNECTION_STRING",
     r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@]+:[^\s@]+@[^\s]+", 0.95),
    ("BEARER_TOKEN", r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{24,}", 0.85),
]

_COMPILED = [(name, re.compile(rx), score) for name, rx, score in KNOWN_PATTERNS]

# Words that, when they sit next to a high-entropy blob, make it a credential.
_SECRET_CONTEXT = (
    "key", "token", "secret", "password", "passwd", "pwd", "credential",
    "apikey", "api_key", "auth", "bearer", "access", "private",
)
_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/=_\-]{24,}\b")
_ENTROPY_WINDOW = 40
_ENTROPY_THRESHOLD = 3.6  # bits/char; English prose sits well below this


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def detect_secrets(text: str) -> list[dict]:
    hits: list[SecretHit] = []
    claimed: list[tuple[int, int]] = []

    for name, pattern, score in _COMPILED:
        for m in pattern.finditer(text):
            grp = 1 if pattern.groups else 0
            hits.append(
                SecretHit(name, m.start(grp), m.end(grp), score, m.group(grp), "known_pattern")
            )
            claimed.append((m.start(), m.end()))

    for m in _CANDIDATE.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in claimed):
            continue
        blob = m.group(0)
        # Long lowercase words are prose, not credentials.
        if blob.isalpha() and blob.islower():
            continue
        ent = shannon_entropy(blob)
        if ent < _ENTROPY_THRESHOLD:
            continue
        window = text[max(0, m.start() - _ENTROPY_WINDOW): m.end() + _ENTROPY_WINDOW].lower()
        if not any(w in window for w in _SECRET_CONTEXT):
            continue
        score = min(0.94, 0.55 + (ent - _ENTROPY_THRESHOLD) * 0.25 + min(len(blob), 60) / 300)
        hits.append(
            SecretHit("HIGH_ENTROPY_SECRET", m.start(), m.end(), round(score, 3),
                      blob, f"shannon_entropy={ent:.2f}")
        )

    hits.sort(key=lambda h: h.start)
    return [h.to_dict() for h in hits]


def redact_secrets(text: str, hits: list[dict]) -> str:
    out = text
    for h in sorted(hits, key=lambda x: -x["start"]):
        out = out[: h["start"]] + f"[REDACTED:{h['secret_type']}]" + out[h["end"]:]
    return out
