# ControlPlane.ai

**Oversight that runs live, not after.**

A risk-adaptive layer that sits between any language model and the business
action it triggers. It scores every response on performance, cost and
responsibility, then decides what to do **before the action commits**.

Team A308 · Accenture Innovation Challenge 2026 · Track 1

```bash
git clone <repo> && cd Project && python run.py
```

That is the whole setup. No Docker, no database server, no API key, no network
after cloning. It opens your browser at `http://localhost:8000`.

---

## The problem

Enterprises can see that their AI is running. They cannot see whether it was
right.

A hallucinated number reads exactly like a correct one. A retry loop looks like
ordinary traffic. Skew only becomes visible across thousands of responses. So
the failure is found by a customer complaint, a month-end invoice, or an audit —
long after someone acted on the answer. Today's monitoring watches uptime,
latency and token counts. Nothing watches the content of what the model
actually said.

## The idea

**Spend checking effort in proportion to risk.** Three rings.

```
        request ──────────────────────────────────────────────────────────►
                                                                    tokens
   ┌────────────────────────────────────────────────────────────────────┐
   │  RING 0 · inline reflex        every response · ~1.5 ms · no 2nd model call │
   │  personal data · credentials · arithmetic · uncertainty ·          │
   │  grounding · cost-vs-baseline          ──►  confidence + verdict   │
   └────────────────────────────────────────────────────────────────────┘
              │ certain violation        │ grey zone
              ▼                          ▼
        block & redact          ┌──────────────────────────────────────┐
        (stream cut             │  RING 1 · deep check   ~6-12% · async │
         mid-sentence)          │  second-opinion model · claim-level   │
                                │  faithfulness · counterfactual twin   │
                                │  spend-capped by an admission gate    │
                                └──────────────────────────────────────┘
                                              │
                                              ▼
                                ┌──────────────────────────────────────┐
                                │  RING 2 · humans & fleet learning     │
                                │  review queue · overrides · threshold │
                                │  tuner · trust report                 │
                                └──────────────────────────────────────┘
```

Two rules decide everything:

**Certainty decides the kind of response.** A card number that passes its Luhn
checksum is not a probability — block and redact it. Everything else is
probabilistic, and a probability never stops a user seeing an answer.

**Reversibility decides the severity.** The same uncertain claim is a footnote
in a draft and an incident in a payment instruction.

| situation | what happens |
|---|---|
| deterministic violation — personal data, credential | block and redact, mid-stream |
| probabilistic, reversible — a draft a human reads | annotate the claim, never block |
| probabilistic, irreversible — payment, email, record write | **gate the commit, not the tokens** |
| mechanical error — arithmetic that does not add up | attach the correction, never rewrite substance |

---

## What is actually built

Everything below runs. Nothing is a mock-up.

**Ring 0, measured at ~1.5 ms median on 100% of traffic**
- Personal-data detection with real validators — Luhn for cards, Verhoeff for
  Aadhaar, issuance rules for US social security numbers. A random 16-digit
  reference is *not* flagged as a card, and we can say why.
- Credential detection: vendor key shapes plus Shannon-entropy detection for
  formats that did not exist when the patterns were written.
- Arithmetic re-derivation. If the answer shows its working, we redo the sum.
- Uncertainty from the model's own token log-probabilities where the provider
  exposes them, with a clearly-labelled lexical fallback where it does not.
- Claim-level grounding **with citations**: each claim is matched to a source
  sentence, and a claim that contradicts one is reported as
  *"answer says 45 days, source says 30 days"* rather than as a similarity score.
- A cost lane: spend anomalies against a per-intent baseline, retry-loop
  detection, and over-modelling ("this lookup was served by a frontier model").
- **Mid-stream interception**: generation is cut off the moment a leak appears,
  not cleaned up afterwards.

**Ring 1, budget-enforced in code**
- A cheaper verifier model re-derives the answer independently, returns strict
  JSON, and gets exactly one stricter retry before being recorded as unavailable
  rather than guessed.
- RAGAS-style atomic-claim faithfulness, so one true half of a sentence cannot
  carry a false half.
- **Counterfactual twin probe** that diffs the *consequential* fields — verdict,
  amounts, rate, conditions — not just text similarity.
- An admission controller enforcing both a traffic cap and a spend cap per
  policy, with in-flight spend reserved so a burst cannot slip past. Deferred
  work is recorded, never silently dropped.
- Results cached by prompt hash, so a re-run during Q&A resolves instantly.

**Ring 2**
- Risk-ordered review queue; a held commit outranks a flagged draft.
- A threshold tuner that moves a threshold only on a **run** of reviewer
  decisions about one signal, tightens **immediately** on a single miss, and
  **never** touches a deterministic check. Every change is logged with a
  readable reason.
- A flag-rate service level per policy, so the loop targets a workload reviewers
  can actually sustain instead of ratcheting forever.
- A **trust report** with precision, recall and false-positive rate — where
  false negatives come from deliberately deep-checking a sample of responses
  Ring 0 *allowed*, because a system that cannot find its own misses is marking
  its own homework.

**Governance**
- Per-use-case policy with a jurisdiction field (EU AI Act / India DPDP), because
  regulation differs by geography and hard-coded rules age badly.
- A per-response evidence record and a plain-text compliance export.
- Conversation-level risk that compounds across turns, so an answer used as the
  premise for three more turns is held to a higher standard.

---

## Run it

**Needs:** Python 3.10+. That is all.

```bash
python run.py            # sets up, builds, starts, opens the browser
python run.py --no-seed  # start with an empty dashboard
python run.py --dev      # auto-reload while editing backend code
```

Windows: double-click `start.bat`. Mac/Linux: `./start.sh`.

**Verify it:**

```bash
.venv/Scripts/python -m pytest backend                                   # 71 tests, ~5s
.venv/Scripts/python backend/tests/smoke.py http://localhost:8000        # every demo scenario
.venv/Scripts/python backend/tests/demo_loop_check.py http://localhost:8000  # the learning loop
```

**Use a real model:** copy `.env.example` to `.env`, add `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`, restart. Same checks, same thresholds, same records — that
is what model-agnostic means. Offline, a simulated provider produces realistic
enterprise answers *including deliberately flawed ones*, so the detectors have
something real to catch with no network access.

**Scale it:** point `DATABASE_URL` at Postgres and `REDIS_URL` at Redis and the
same code runs across replicas. Neither is needed for the prototype.

**Containers:** `docker compose up --build` runs the same single service. This is
an alternative, not the supported path — `python run.py` is what we test and
demo on, and it is the one we have exercised end to end.

---

## Screens

| Screen | What it is for |
|---|---|
| **Live oversight** | Send a prompt, watch the answer stream and the verdict land beside it. Live traffic table, latency and budget tiles. |
| **Review queue** | Everything waiting on a human, risk-ordered. Accept / reject / edit, each one a labelled example. |
| **Bias mirror** | Type any question about a person; see both answers side by side and exactly which decision fields changed. |
| **Policy console** | Sliders per use case. Move one and the next request behaves differently. History chart of every threshold change and why. |
| **Trust report** | Precision, recall, false-positive rate, confusion matrix, flag rate against service level — with sample sizes and honest caveats. |
| **Evidence record** | The full trace behind one verdict: every signal with character offsets and validators, the deep check, the human decision, the thresholds it moved. |

---

## Design decisions worth defending

**We do not use a neural entailment model for grounding by default.**
Vectara's HHEM and similar cross-encoders are the standard answer. They are
supported as an optional backend, but the default is a deterministic evidence
matcher, because a reviewer needs to be told *why*. "Entailment probability
0.31" is not a reason. "The answer says 45 days, the source says 30 days,
section 2" survives being read out in a compliance meeting. It is also 20×
faster, which is what lets Ring 0 run on 100% of traffic.

**Presidio is optional, not the default.** Microsoft Presidio is excellent and
we support it (`PII_ENGINE=hybrid`), but its default pipeline costs ~20 ms and a
400 MB model download. Our recognizer registry runs in under a millisecond and
carries real checksums, so a hit is evidence rather than a score. Presidio
layers on top for free-text names and places when you want it.

**The protected-attribute list is a readable list, on purpose.** A learned
classifier would have broader coverage and would be much harder to defend in an
audit. Anyone can read exactly which substitutions the bias probe makes and
challenge them.

**Ungroundable is reported, never scored as correct.** There is often no
reliable ground truth. Saying so is the honest answer; scoring it 1.0 would be a
lie that passes. It escalates only where the policy's risk appetite says it
should — flagging every unverifiable answer is the fastest route to reviewers
ignoring the system.

**Nothing fails open silently.** Every detector is wrapped: a crash degrades the
verdict to "unverified" and escalates, and the outage is named in the record.

---

## Repository

```
run.py               one command to run everything
backend/app/
  core/orchestrator.py   the whole request journey, in one readable file
  core/llm_gateway.py    one interface in front of every provider
  rings/ring0/           the six inline detectors + the decision engine
  rings/ring1/           verifier, faithfulness, counterfactual, budget, worker
  rings/ring2/           threshold tuner, trust metrics
  api/                   generate, policy, review, audit, metrics, demo
frontend/src/          the dashboard (built output committed, so no Node needed)
seed/                  demo prompts and the fake source-of-truth documents
docs/                  architecture, demo script, business proposal
PROJECT_MANUAL.md      plain-language guide to everything above
```

## Documentation

- **[PROJECT_MANUAL.md](PROJECT_MANUAL.md)** — everything explained in plain
  words, including a step-by-step demo script and a troubleshooting section.
- **[docs/architecture.md](docs/architecture.md)** — data flow, schema, and the
  decision logic in detail.
- **[docs/demo_script.md](docs/demo_script.md)** — the four-minute run-through.
- **[docs/business_proposal.md](docs/business_proposal.md)** — problem framing,
  target users, business case, roadmap, risks.

## Demo video

_(link to be added)_

## Team

**A308** — Kshitij Pramod Ramtekkar and Utkarsh Kashyap, IIT Kanpur.
