# ControlPlane.ai — Architecture

## Where the checker sits

Between the model and the action. Not a pre-response gate (that adds latency to
every answer), not a post-hoc audit (that finds problems after someone acted).
Inline middleware that runs *beside* the token stream.

```
[client]  POST /v1/generate  {prompt, use_case, context_docs,
                              is_reversible, downstream_action, session_id}
    │
    ▼
[ControlPlane gateway] ──stream──► [OpenAI | Anthropic | offline simulator]
    │                                        │
    │◄───────────────────────── tokens ──────┘
    │
    ├─► tokens forwarded to the client immediately (Server-Sent Events)
    │
    ├─► every 12 chunks: deterministic re-scan of the partial text
    │      └─ credential or validated identity number present?
    │            └─ CUT THE STREAM. Redact. Record. Done.
    │
    ▼  (on completion)
┌──────────────────────── RING 0 · ~1.5 ms median ────────────────────────┐
│  pii.py            recognizer registry + checksums (Luhn/Verhoeff/SSN)  │
│  secrets.py        vendor patterns + Shannon entropy near key-words     │
│  schema_check.py   re-derive every stated equation; JSON shape check    │
│  uncertainty.py    token log-prob entropy, or a labelled lexical proxy  │
│  grounding.py      claim → source sentence, with numeric contradiction  │
│  cost.py           z-score vs per-intent baseline, retry, over-modelling│
└─────────────────────────────┬───────────────────────────────────────────┘
                              ▼
                    scorer.py  →  confidence = 1 − Σ weighted penalties
                              │   action = f(certainty, reversibility)
                              │
    ┌─────────────────────────┼──────────────────────────┐
    ▼                         ▼                          ▼
  block                  allow / edit / flag           gate
  redact, done           delivered now                 tokens delivered,
                                                       commit held
                              │
                              ▼  (async, never blocking)
                    budget.admit(policy, priority)
                              │ admitted          │ over cap
                              ▼                   ▼
┌────────── RING 1 ──────────┐              ring1_status =
│ verifier_judge   2nd model │              "deferred", recorded
│ retrieval_check  atomic    │              and shown in the UI
│ counterfactual   twin      │
└────────────┬───────────────┘
             ▼
   verdict updated in DB ──► event bus ──► WebSocket ──► dashboard
             │
             ▼
┌────────── RING 2 ─────────────────────────────────────────┐
│ review queue (risk-ordered) → override → threshold_tuner  │
│                                       → trust_metrics     │
└───────────────────────────────────────────────────────────┘
```

## The decision engine

`rings/ring0/scorer.py`. Signals in, one verdict out.

**Confidence** starts at 1.0 and subtracts weighted penalties:

| signal | weight | fires when |
|---|---|---|
| grounding | 0.42 | coverage below the policy floor, or any contradicted claim |
| secret | 0.40 | credential detected |
| pii | 0.35 | personal data detected |
| arithmetic | 0.30 | a stated equation does not recompute |
| uncertainty | 0.22 | token entropy above the policy ceiling |
| schema | 0.20 | output does not match the required shape |
| ungroundable | 0.18 × risk factor | no source documents were supplied |
| conversation | ≤0.15 | earlier turns in this session were flagged |

**Action** is then chosen by two independent axes:

```
                        reversible              irreversible
deterministic           block & redact          block & redact
probabilistic           flag (annotate)         gate the commit
mechanical error        edit (attach fix)       gate the commit
clean                   allow                   allow
```

Plus a confidence floor: below `policy.confidence_block_threshold` the response
is held regardless of which individual signal was responsible.

`driving_signal` records which penalty dominated. Ring 2 needs it to know which
threshold a human just disagreed with.

## Grounding, in detail

The part most likely to be asked about.

1. **Split** both the answer and each source document into sentences; long
   compound sentences are split further so a true half cannot carry a false half.
2. **Index** the source sentences with inverse-document-frequency weights, and
   extract every (value, unit) quantity — durations, percentages, money, counts.
3. For each claim:
   - find the best-matching source sentence by IDF-weighted overlap;
   - for every quantity in the claim, check whether it appears in that passage
     (full credit), elsewhere in the corpus (partial credit), or nowhere;
   - if nowhere, look for a **rival**: a source sentence sharing at least two
     content words that states a *different* value for the *same* unit. That is
     a contradiction, and it is reported with the source sentence quoted.
4. `support = 0.45 × lexical + 0.55 × numeric` for claims containing figures,
   lexical overlap alone otherwise.
5. Coverage is the mean, minus 0.25 per contradicted claim — a contradiction is
   worse than a gap and should not be averaged away.

Claims that state an intention or an opinion ("I would send this today") are
labelled non-factual rather than scored, so coverage is not diluted by text that
was never checkable.

**No source documents** returns `score: None, status: "ungroundable"`. Never 1.0.

## Ring 1 admission control

`rings/ring1/budget.py`, per policy, over a rolling one-hour window.

- **Volume cap** — at most `ring1_sample_rate` of requests.
- **Spend cap** — at most `ring1_spend_cap_pct` of what the production model
  spent, with the estimated cost of in-flight checks reserved at admission so a
  burst cannot all pass the check before the first one pays.
- Both stay dormant until 5 checks have run in the window: a percentage cap is
  meaningless on a window of three requests, where one indivisible check is
  already 30% of spend.
- Responses feeding an **irreversible** action (priority ≥ 1.0) bypass the volume
  cap. Deferring that check to save a fraction of a cent is the wrong trade; the
  spend cap still bounds the damage.
- ~20% of the budget is reserved for auditing responses Ring 0 **allowed**,
  which is the only way false negatives get measured.

## The learning loop

`rings/ring2/threshold_tuner.py`. Two loops at different speeds.

**Fast loop (per signal).** Over the last 5 reviewer decisions on one signal for
one policy: if more than 30% say we over-flagged, loosen that threshold by 0.05.
A single false negative — a reviewer rejecting something we *allowed* — tightens
immediately, because the costs are not symmetric.

**Slow loop (per policy).** If the flag rate over the last 100 responses exceeds
1.5× the policy's flag-rate service level, loosen. Rate-limited to one correction
per hour per policy so the loop settles instead of oscillating. It defers to the
fast loop and only speaks when the fast loop had nothing to say.

**Never tuned:** personal-data thresholds, credential detection, arithmetic.
These are checks we can prove, and negotiating them away on reviewer sentiment
would hollow out the product. `NOT_TUNABLE` names each one and why.

All thresholds are clamped to sane bounds. Every change writes a
`threshold_adjustments` row with old value, new value, trigger and a
human-readable reason.

## Data model

| table | holds |
|---|---|
| `llm_responses` | one row per model call: prompt, raw and redacted text, tokens, cost, latency, all Ring 0 signals as JSON, confidence, action, Ring 1 result, gate state. **This row is the audit record.** |
| `policies` | per use case: jurisdiction, risk tolerance, latency budget, every threshold, sample and spend caps, flag-rate service level, blocked entity types. |
| `overrides` | reviewer decision, the signal that drove the machine verdict, the machine action at the time. The labelled training data. |
| `threshold_adjustments` | every threshold change, automatic or manual, with its reason. |
| `conversations` | per session: turn count, flagged turns, exponentially-decayed accumulated risk. |

SQLite by default with write-ahead logging; the same SQLAlchemy models run on
Postgres by changing one environment variable.

## Model-agnosticism, concretely

`core/llm_gateway.py` exposes one method. Every provider — OpenAI, Anthropic,
the offline simulator — returns the same `GenerationResult`: text, tokens in and
out, latency, cost, and token log-probabilities where available. Nothing
downstream knows or cares which provider produced it.

Where a provider does not expose log-probabilities, `uncertainty.py` detects
that and switches to a lexical estimator that is explicitly labelled as weaker
in the record and in the UI. Degrading is fine; degrading silently is not.

## Failure behaviour

Every Ring 0 detector runs inside `_safe()`. A crash yields a fallback value
plus a named degradation, the verdict escalates from `allow` to `flag`, and the
outage appears in the evidence record. Ring 1 failures mark the response
`ring1_status = "failed"` and leave it flagged for a human. A verifier that
cannot be parsed is recorded as unavailable — never as agreement.

An unavailable check must never look like a passed check.

## Scale

Reference figure: tens of thousands of interactions a week across several use
cases. Ring 0 is pure CPU at ~1.5 ms, so a single core sustains hundreds of
requests a second; it is not the bottleneck, the model call is. Ring 1 is queued
and rate-limited by construction. The event bus and job queue have Redis
implementations behind the same interface for running across replicas, and the
load simulator (`POST /v1/demo/simulate`) replays traffic through the real
pipeline so the latency percentiles on the dashboard are measured rather than
asserted.
