"""Ring 0 - personal data detection.

"PII" means personally identifiable information: anything that identifies a
real person, on its own or combined with something else.

Design choice, and it is a deliberate one
-----------------------------------------
The obvious move is to hand this to a named-entity model (Presidio + spaCy).
We support that, but it is NOT the default, for three reasons:

  1. Latency. A spaCy pass costs ~20 ms warm. Our Ring 0 budget is single-digit
     milliseconds because it runs on 100% of traffic.
  2. Weight. The default Presidio pipeline pulls a 400 MB language model. A
     judge cloning this repo should not wait for that.
  3. Auditability. A regulator asking "why did you block this?" is better
     served by "digits 41-52 are a 12-digit number passing the Aadhaar Verhoeff
     checksum" than by "a neural network scored 0.85".

So the default engine is a registry of pattern recognizers, most of which carry
a real validator (Luhn, Verhoeff, IBAN mod-97, SSN issuance rules). Checksums
are what turn a regex into evidence: a random 16-digit string is not a card
number, and we can prove the difference.

Set PII_ENGINE=hybrid to layer Presidio's NER on top for free-text names and
locations. When it is present its findings are merged in and labelled with
their source, so the evidence drawer always shows which engine said what.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from functools import lru_cache

from app.core.config import settings

log = logging.getLogger("controlplane.ring0.pii")


@dataclass
class PIIHit:
    entity_type: str
    start: int
    end: int
    score: float
    text: str
    engine: str = "deterministic"
    validator: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Never echo the raw sensitive value into the audit log: store a
        # masked preview only. The full value stays in the response row, which
        # is access-controlled, and is what gets redacted before display.
        d["text"] = mask(self.text)
        return d


def mask(value: str) -> str:
    keep = 2 if len(value) <= 8 else 4
    if len(value) <= keep:
        return "*" * len(value)
    return value[: keep // 2] + "*" * (len(value) - keep) + value[-(keep - keep // 2):]


# --------------------------------------------------------------------------
# validators
# --------------------------------------------------------------------------
def luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13:
        return False
    total, alt = 0, False
    for n in reversed(d):
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_ok(digits: str) -> bool:
    """Checksum used by India's Aadhaar number."""
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) != 12:
        return False
    c = 0
    for i, n in enumerate(reversed(d)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][n]]
    return c == 0


def iban_ok(value: str) -> bool:
    v = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", v):
        return False
    rearranged = v[4:] + v[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


def ssn_ok(value: str) -> bool:
    """US SSN issuance rules: area/group/serial may not be all zeros, area may
    not be 666 or 900-999."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in ("000", "666") or area[0] == "9":
        return False
    return group != "00" and serial != "0000"


# --------------------------------------------------------------------------
# recognizer registry
# --------------------------------------------------------------------------
@dataclass
class Recognizer:
    entity_type: str
    pattern: re.Pattern
    base_score: float
    validator_name: str = ""
    validator = None
    # A nearby keyword raises confidence -- context is what separates "a
    # 12-digit number" from "an Aadhaar number".
    context_words: tuple[str, ...] = ()
    context_boost: float = 0.15
    requires_context: bool = False


def _r(entity_type, pattern, base_score, validator=None, validator_name="",
       context_words=(), requires_context=False, boost=0.15) -> Recognizer:
    rec = Recognizer(
        entity_type=entity_type,
        pattern=re.compile(pattern, re.I),
        base_score=base_score,
        validator_name=validator_name,
        context_words=context_words,
        context_boost=boost,
        requires_context=requires_context,
    )
    rec.validator = validator
    return rec


RECOGNIZERS: list[Recognizer] = [
    _r("EMAIL_ADDRESS", r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b", 0.95),
    _r("CREDIT_CARD", r"\b(?:\d[ -]?){13,19}\b", 0.55, luhn_ok, "luhn",
       ("card", "credit", "debit", "visa", "mastercard", "expiry", "cvv", "ending")),
    _r("AADHAAR", r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b", 0.55, verhoeff_ok, "verhoeff",
       ("aadhaar", "aadhar", "uidai", "uid")),
    _r("US_SSN", r"\b\d{3}-\d{2}-\d{4}\b", 0.80, ssn_ok, "ssn_issuance_rules",
       ("ssn", "social security", "tin")),
    _r("PAN_IN", r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.75, None, "pan_format",
       ("pan", "permanent account", "income tax")),
    _r("IBAN", r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", 0.55, iban_ok, "iban_mod97",
       ("iban", "account", "bank", "swift")),
    _r("IFSC", r"\b[A-Z]{4}0[A-Z0-9]{6}\b", 0.80, None, "ifsc_format",
       ("ifsc", "bank", "branch", "neft", "rtgs")),
    _r("BANK_ACCOUNT", r"\b\d{9,18}\b", 0.35, None, "",
       ("account", "a/c", "acct", "beneficiary"), requires_context=True, boost=0.45),
    _r("PHONE_NUMBER",
       r"(?:\+91[ -]?)?\b[6-9]\d{4}[ -]?\d{5}\b|\+\d{1,3}[ -]?\d{3}[ -]?\d{3}[ -]?\d{4}\b",
       0.60, None, "", ("phone", "mobile", "contact", "call", "whatsapp", "tel")),
    _r("IP_ADDRESS", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", 0.55),
    _r("DATE_OF_BIRTH", r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", 0.35, None, "",
       ("born", "dob", "date of birth", "birthday"), requires_context=True, boost=0.5),
    _r("PASSPORT", r"\b[A-PR-WY][0-9]{7}\b", 0.40, None, "",
       ("passport",), requires_context=True, boost=0.45),
    _r("MEDICAL_RECORD", r"\b(?:MRN|UHID)[:\s-]*[A-Z0-9]{5,12}\b", 0.85, None, "",
       ("patient", "mrn", "uhid", "record")),
    _r("POSTAL_ADDRESS",
       r"\b\d{1,4}[\w .'-]{3,40}(?:Marg|Road|Rd|Street|St|Avenue|Ave|Lane|Nagar|Colony|Sector)\b"
       r"[\w ,.'-]{0,30}\d{6}\b",
       0.70, None, "", ("address", "residing", "registered at", "located")),
    # Deliberately low-confidence on its own: a labelled name is a strong hint
    # but never enough to block by itself.
    _r("PERSON_NAME", r"(?:Customer|Client|Patient|Applicant|Mr\.?|Ms\.?|Mrs\.?)[:\s]+"
                      r"([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})", 0.55, None, "labelled_name"),
]

_WINDOW = 48


def _has_context(text: str, start: int, end: int, words: tuple[str, ...]) -> bool:
    if not words:
        return False
    window = text[max(0, start - _WINDOW): min(len(text), end + _WINDOW)].lower()
    return any(w in window for w in words)


def _overlaps(a: PIIHit, b: PIIHit) -> bool:
    return a.start < b.end and b.start < a.end


def _dedupe(hits: list[PIIHit]) -> list[PIIHit]:
    """Keep the highest-scoring hit for any overlapping span."""
    ordered = sorted(hits, key=lambda h: (-h.score, h.start, -(h.end - h.start)))
    kept: list[PIIHit] = []
    for h in ordered:
        if not any(_overlaps(h, k) for k in kept):
            kept.append(h)
    return sorted(kept, key=lambda h: h.start)


def detect_pii_deterministic(text: str) -> list[PIIHit]:
    hits: list[PIIHit] = []
    for rec in RECOGNIZERS:
        for m in rec.pattern.finditer(text):
            raw = m.group(1) if rec.pattern.groups else m.group(0)
            start = m.start(1) if rec.pattern.groups else m.start()
            end = m.end(1) if rec.pattern.groups else m.end()

            score = rec.base_score
            validator = ""
            if rec.validator is not None:
                if not rec.validator(raw):
                    continue  # failed its checksum -- not this entity type
                score = min(0.99, score + 0.40)
                validator = rec.validator_name
            elif rec.validator_name:
                validator = rec.validator_name

            ctx = _has_context(text, start, end, rec.context_words)
            if rec.requires_context and not ctx:
                continue
            if ctx:
                score = min(0.99, score + rec.context_boost)

            hits.append(
                PIIHit(rec.entity_type, start, end, round(score, 3), raw,
                       "deterministic", validator)
            )
    return _dedupe(hits)


@lru_cache(maxsize=1)
def _presidio_engine():  # pragma: no cover - optional dependency
    from presidio_analyzer import AnalyzerEngine

    return AnalyzerEngine()


def detect_pii_presidio(text: str) -> list[PIIHit]:  # pragma: no cover - optional
    try:
        results = _presidio_engine().analyze(text=text, language="en")
    except Exception as exc:
        log.warning("presidio unavailable (%s); deterministic engine only", exc)
        return []
    return [
        PIIHit(r.entity_type, r.start, r.end, round(float(r.score), 3),
               text[r.start:r.end], "presidio", "spacy_ner")
        for r in results
        if r.score >= 0.4
    ]


def detect_pii(text: str, engine: str | None = None) -> list[dict]:
    """Public entry point. Returns plain dicts so it is JSON-serialisable."""
    mode = engine or settings.pii_engine
    hits = detect_pii_deterministic(text)
    if mode == "hybrid":
        extra = [h for h in detect_pii_presidio(text)
                 if h.entity_type in ("PERSON", "LOCATION", "NRP", "DATE_TIME")]
        hits = _dedupe(hits + extra)
    return [h.to_dict() for h in hits]


def redact(text: str, hits: list[dict], entity_types: list[str] | None = None) -> str:
    """Replace sensitive spans with a typed placeholder.

    Redaction is mechanical -- it never rewrites the substance of an answer,
    which is the line the action matrix draws between "repair" and "rewrite".
    """
    targets = [h for h in hits
               if entity_types is None or h["entity_type"] in entity_types]
    out = text
    for h in sorted(targets, key=lambda x: -x["start"]):
        out = out[: h["start"]] + f"[REDACTED:{h['entity_type']}]" + out[h["end"]:]
    return out
