# ControlPlane.ai — Business Proposal

Team A308 · Accenture Innovation Challenge 2026 · Track 1

---

## 1. Problem framing

Enterprises now run generative AI across many use cases at once — customer
support assistants, internal copilots, decision-support tools inside regulated
workflows. Each carries a different risk signature depending on the model, the
data it draws on, and how its output is used downstream.

What they can see is that the AI is running: uptime, latency, token counts. What
nobody watches is **the content of what the model actually said**.

Three failure modes, none of which announce themselves:

- **Confidently wrong.** A hallucinated number reads exactly like a correct one,
  and the model offers no honest signal of its own doubt.
- **Quietly expensive.** Retries, oversized models and bloated context leak spend
  that no dashboard attributes to anything.
- **Biased, unsafe or leaking.** Harm is systemic rather than per-answer. Read
  one loan recommendation and it looks like reasoning; the skew is only visible
  across responses.

So failure is discovered by a customer complaint, a month-end invoice, or an
audit — days to weeks after someone acted on the answer.

### Why this is hard, not just unbuilt

The brief names the complications, and they are the ones that break naive
designs:

| Complication | What it rules out |
|---|---|
| Use cases differ in risk tolerance and latency budget | One uniform checker |
| Bias, hallucination and privacy overlap | Clean per-category detectors |
| Often no reliable real-time ground truth | Verification that assumes a source |
| Over-flagging causes alert fatigue; under-flagging causes liability | A fixed threshold |
| Multi-turn agents compound risk | Per-response-only evaluation |
| Regulation varies by geography and keeps changing | Hard-coded rules |
| The model is consumed via API | Anything needing model internals |

Our design answers each of these directly, and section 6 says where.

## 2. Solution design

**A model-agnostic layer between any language model and the business action it
triggers.** It scores every response on performance, cost and responsibility,
then decides what to do before the action commits.

The organising principle: **spend checking effort in proportion to risk.**

**Ring 0 — inline reflex.** Every response, ~1.5 ms measured, no second model
call. Reads signals the first inference already produced: token-level
uncertainty, claim-level grounding against source documents, arithmetic
re-derivation, validated personal-data and credential detection, and token spend
against a per-intent baseline.

**Ring 1 — conditional deep check.** Roughly 6–12% of traffic, asynchronous,
budget-capped. A cheaper verifier re-derives the answer independently, claims are
checked one at a time against the sources, and a counterfactual twin — the same
prompt with one attribute swapped — exposes bias no single response can reveal.

**Ring 2 — escalation and fleet learning.** A risk-ordered reviewer queue, a
per-response audit trail, and thresholds retuned from human verdicts with every
change logged and explainable.

### The decision rule

Two axes, and this is what makes the system usable rather than merely thorough:

**Certainty decides the kind of response.** A card number passing its Luhn
checksum is not a probability. Block and redact it, mid-stream. Everything else
is probabilistic, and a probability never stops a user seeing an answer.

**Reversibility decides the severity.** The same uncertain claim is a footnote in
a draft and an incident in a payment instruction. So: annotate the reversible,
**gate the commit — not the tokens — on the irreversible.** The answer never
waits. Only the action does.

## 3. Target users

| Who | Today | With ControlPlane |
|---|---|---|
| **AI platform / LLMOps teams** | Accountable when a model misbehaves, with a postmortem | Live control, and a measured service level on AI behaviour |
| **Risk, compliance and audit** | Anecdotes and spot checks | Per-response evidence — the log a DPDP or EU AI Act reviewer asks for |
| **Frontline staff** — agents, analysts, underwriters | No idea which answers to trust | Confidence and sources inline; corrections attached, not silent rewrites |
| **CIO / FinOps** | A single opaque model bill | Spend attributed by use case, intent and model, with recoverable spend named |

The buyer is usually the platform team; the budget often comes from the CIO,
because the cost lane makes the business case without needing a risk incident.

## 4. Business case

**The cost lane pays for the other two.**

Two recoverable line items, both measured by the prototype today:

- **Over-modelling.** Routine lookups served by frontier models. In the demo
  fleet, the cost lane names the difference between what each call cost and what
  the smallest adequate model would have cost.
- **Retry loops.** Near-identical prompts inside one session, which almost always
  mean the previous answer was unusable. Invisible in a token dashboard because
  they look like ordinary traffic.

**Oversight is deliberately cheap.** Ring 0 makes no second model call, so it is
CPU-only. Ring 1 is capped in code at a few percent of inference spend, enforced
by an admission controller rather than promised in a slide. Measured on the demo
fleet: 2–3% of inference spend.

Against that: one wrong customer-facing quote, one regulatory finding, or one
discriminatory lending decision costs more than a year of oversight.

**The service levels we would hold ourselves to** — and which the prototype
already measures on its own dashboard:

| Target | Prototype (offline, ~250 responses) |
|---|---|
| Detection: weeks → seconds | Ring 0 median **~1.5 ms**, p99 under 4 ms |
| Added latency the user perceives: ~0 ms | Checks run beside the stream; only commits wait |
| Oversight under 3% of inference spend | **2–3%**, capped by admission control |
| 100% of responses logged with a verdict | 100%, with reasons and signal spans |

## 5. Phased roadmap

**Phase 0 — Prototype (done).** Three rings running end to end on a simulated
fleet: six inline detectors, three deep checks, the human loop, the policy
console, the trust report. 71 automated tests. One command to run.

**Phase 1 — Shadow mode (weeks 1–6).** Deploy alongside one real use case.
Observe only: no blocking, no gating. Calibrate thresholds against real traffic
and build the first labelled set from reviewer decisions. Deliverable: a measured
false-positive and false-negative rate for that use case, and an honest
statement of what the checker does and does not catch.

**Phase 2 — Enforce on the reversible (weeks 6–12).** Turn on blocking for
deterministic violations only — personal data and credentials, where a checksum
means we are not guessing. Annotate probabilistic findings. Reviewer queue live.
The learning loop begins moving thresholds.

**Phase 3 — Gate the irreversible (quarter 2).** Extend to workflows that trigger
payments, outbound email and record writes. This is where the commit gate earns
its keep, and where the business case stops being about cost.

**Phase 4 — Fleet (quarter 3+).** Multiple use cases under one policy layer with
per-geography and per-sector configuration. Postgres and Redis backends (already
behind the same interfaces). Trust reporting per policy to the risk committee.

**Phase 5 — Ecosystem.** Optional neural entailment backend for domains where
citation-level grounding is not enough; connectors to existing observability so
verdicts land where teams already look.

## 6. How the design answers the brief's complications

| Complication | Where it is handled |
|---|---|
| Use cases differ in risk and latency budget | `Policy` per use case; every threshold read at request time. Proven in `tests/test_policy.py`: identical signals, different verdicts. |
| Bias, hallucination and privacy overlap | Signals are scored independently and fused in one confidence, rather than forced into exclusive categories. A fabricated detail about a person raises both the grounding and the personal-data penalty. |
| No reliable real-time ground truth | Explicit `ungroundable` state. Never scored as correct. Escalates only where the policy's risk appetite says it should. |
| Over- vs under-flagging | A flag-rate service level per policy, a fast per-signal tuner, and a deliberate asymmetry: a run of false positives loosens, a single miss tightens immediately. |
| Multi-turn and agentic compounding | Per-session accumulated risk that decays but does not reset, plus the commit gate on irreversible actions. |
| Regulation varies and evolves | `jurisdiction` is a policy field, blocked entity types are per-policy data, and every threshold is editable at runtime with an audit trail. |
| Model consumed via API | Everything works at the input/output layer. Where a provider exposes log-probabilities we use them; where it does not we degrade to a labelled weaker estimator rather than pretending. |
| Metrics to a sceptical stakeholder | The trust report: precision, recall, false-positive rate, confusion matrix, with sample sizes and an explicit caveat when the sample is thin. False negatives come from a deliberate audit sample of allowed traffic. |

## 7. Key risks and mitigations

| Risk | Why it is real | Mitigation |
|---|---|---|
| **Alert fatigue** — the checker becomes noise people bypass | The most common failure of this product category | A flag-rate service level per policy, tuned towards by the learning loop; deterministic and probabilistic findings are visually and functionally distinct so a block means something |
| **False confidence** — the checker becomes an alibi | "We have a guardrail" is worse than nothing if it does not work | Every number reported with its sample size; the trust page states when the sample is too thin; the audit sample exists specifically to find our own misses |
| **Detection gaps** — plausible prose with no numbers and no source | Genuinely hard, and no vendor solves it | Reported as unverifiable rather than passed; Ring 1's independent re-derivation is the second line; honest about it in the README |
| **Threshold drift** — the loop tunes itself into uselessness | An unconstrained control loop will | Thresholds clamped to bounds; deterministic checks excluded entirely; the slow loop rate-limited to one correction per hour; every change logged with a reason a human can overrule |
| **Latency creep** | Anything on the request path gets blamed for latency | Ring 0 is CPU-only with a measured p99 under 4 ms, published on the dashboard; Ring 1 is off the request path entirely |
| **Cost creep** | Oversight that costs more than it saves does not survive a budget review | Admission controller with a hard spend cap, in-flight spend reserved, deferred work recorded rather than hidden |
| **Vendor lock-in** | Model choice changes yearly | Single gateway interface; the checks, thresholds and audit schema are identical across providers |
| **Prompt injection targeting the checker** | An adversary who knows the rules can write around them | Deterministic checks operate on output, not instructions, so they cannot be talked out of a checksum; the probabilistic layer is defence in depth, not the only line. A dedicated injection-resistance workstream belongs in Phase 2. |

## 8. What we would build next

Honest about scope. The prototype covers the core mechanism; these are the gaps
we would close first:

1. **Multilingual grounding.** English-only today. Enterprise fleets are not.
2. **Tables and structured sources.** Grounding works on prose; a lot of
   enterprise ground truth is a spreadsheet.
3. **Learned attribute discovery for the bias probe**, kept auditable — surfacing
   candidate attributes for a human to approve into the list, rather than
   replacing the list with a model.
4. **Injection-resistance testing** as a first-class workstream.
5. **Reviewer ergonomics.** The queue works; at real volume it needs batching,
   keyboard-driven triage and delegation.
