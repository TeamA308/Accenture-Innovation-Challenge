# Demo script — 4 minutes

Rehearse with `python backend/tests/smoke.py http://localhost:8000` first; it
walks every scenario below and prints what each one did.

**Setup:** `python run.py`, wait for the browser, confirm the bottom table has
rows. If it is empty, click **Replay 120 requests**. Stay on **Live oversight**.

---

## 0:00 — Frame it (15s)

> "Enterprises can see that their AI is running. They cannot see whether it was
> right. A hallucinated number reads exactly like a correct one, and the failure
> gets found weeks later by a complaint or an audit. This sits between the model
> and the action, and checks every answer before anyone acts on it."

---

## 0:15 — A leak, stopped mid-sentence (45s)

**Do:** demo prompt → *"Personal data and a live credential leak"* → **Send**.

**Watch for:** the answer starts writing a customer record and **stops
mid-sentence**.

> "The model was still typing when we cut it off. Aadhaar number, card number,
> live API key. We did not detect this afterwards — we stopped it happening."

**Do:** point at the red **block** badge, read a reason containing
*validated by verhoeff*.

> "This is not a regex that saw twelve digits. It ran the Aadhaar checksum. A
> random twelve-digit invoice reference would not be touched. That is the
> difference between a guess and evidence."

**Do:** point at the **Ring 0 median latency** tile.

> "About a millisecond and a half, on one hundred percent of traffic, with no
> second model call."

---

## 1:00 — Confidently wrong, caught with a citation (60s)

**Do:** demo prompt → *"Confidently wrong against the source policy"* → **Send**.

**Watch for:** a fluent, complete, entirely wrong answer. Orange **flag**.

> "It says the refund window is forty-five days. The policy document we handed
> it says thirty. The system is not saying 'this looks uncertain' — it can point
> at the sentence it contradicts."

**Do:** point at the **ring 1 running** spinner, wait ~1 second for it to land.

> "The user already has their answer. The deep check runs beside them, and a
> second, cheaper model just re-derived it from the source and disagreed."

**Do:** **Open evidence →**, stay on the **Ring 0 signals** tab, scroll to the
claims list.

> "Green is supported with the source sentence quoted underneath. Red is
> contradicted, with both numbers named. This is the record a DPDP or EU AI Act
> reviewer asks for — every signal, its score, the exact characters it fired on."

---

## 2:00 — Same prompt, two policies (25s)

**Do:** back to **Live oversight**. Run *"Policy contrast, run 1 of 2"*, note the
verdict and confidence. Then run *"Policy contrast, run 2 of 2"*.

> "Identical prompt, identical answer, identical signals. Different use case,
> different verdict — the regulated policy holds it, the internal one lets it
> through with a note. That is the product: one checker, several risk appetites."

---

## 2:25 — Bias you can actually see (45s)

**Do:** sidebar → **Bias mirror** → **Run the twin**.

> "Same income, same six years of employment, same clean repayment record. One
> name changed."

**Do:** point at the two answers, then at **What actually changed**.

> "Approved in full at ten and a half percent for Rohan. Seven lakh instead of
> twelve, at thirteen and a quarter, with a guarantor demanded, for Priya. You
> would never see this reading one answer at a time — and reading one answer at
> a time is exactly what every review process does."

**Do:** scroll to the attribute list.

> "Deliberately a readable list, not a learned classifier. Anyone can inspect
> which swaps we make and challenge them. A bias probe nobody can audit is not
> evidence of anything."

---

## 3:10 — Gating an irreversible action (20s)

**Do:** **Live oversight** → *"Irreversible action: a vendor payment"* → **Send**.

**Watch for:** blue **gate commit** and **commit held**.

> "The person reads every word. What is held is the payment. The answer never
> waits — only the action does. Had this been a draft, the same signals would
> have produced a note, not a hold."

---

## 3:30 — The loop closes, live (60s)

**Do:** **Policy console** → *Internal knowledge copilot*. Note **Grounding
floor 0.45** and the empty history chart.

**Do:** **Review queue** → expand the first item → read why it was flagged →
**Accept — we over-flagged**. Repeat on four more (pick ones whose reason
mentions *grounding coverage*).

> "Watch the banner: 'not enough evidence to move a threshold yet'. One
> reviewer's opinion is not evidence. It wants a run."

**Watch for:** on the fifth, the banner changes to **threshold moved**. Read the
reason aloud.

**Do:** back to **Policy console**. Slider now 0.40; the chart has a step in it.

> "Five human decisions, one threshold change, a written reason attached. The
> feedback loop, running — not a bullet point."

**Do:** point at the blocked-entity list.

> "And note what did *not* move. A checksum-validated card number is not up for
> negotiation, however many times a reviewer disagrees."

---

## 4:30 — Cost, and honesty (25s)

**Do:** **Live oversight**, point at **Recoverable spend** and **Oversight
budget**.

> "Oversight costs about three percent of what the model itself costs, capped by
> an admission controller you can read. And the cost lane has already found more
> recoverable spend than the checking costs. It pays for itself."

**Do:** **Trust report**.

> "And this is the slide nobody shows you. Of what we flagged, how much was
> really a problem. How many we missed — measured, by deliberately re-checking
> answers we allowed, because a system that cannot find its own misses is marking
> its own homework. Every number with its sample size, and it tells you when the
> sample is too small to trust."

---

## Close (10s)

> "Every answer carries a verdict, a reason and a record. The rules differ by
> risk. The humans teach it. And it tells you honestly how well it is doing."

---

## Recovery, if something goes wrong on stage

| symptom | do this |
|---|---|
| Deep check says **deferred** | Budget full — that is the cap working. Say so, then click **Clear traffic** and resend. |
| Answer streams but no verdict | Reload the page and resend. Check the `live` indicator top-right of the composer. |
| Override moves no threshold | The item's driving signal was deterministic (arithmetic or personal data), which is never tuned. Pick items whose reason says *grounding coverage*. |
| Dashboard empty | **Replay 120 requests**. |
| Anything else | Ctrl+C, `python run.py`, ~20 seconds. |

## Likely questions

**"How is this different from Guardrails AI or NeMo Guardrails?"**
Those are static validators — a fixed rule set applied uniformly. The three
differences: checking effort scales with risk and reversibility, so an internal
note is not policed like a payment; the loop learns from human overrides with
every change logged and explainable; and the cost lane funds the other two.

**"Why not a neural entailment model for grounding?"**
Supported as an optional backend, not the default. A reviewer needs to be told
*why*. "Entailment 0.31" is not a reason; "the answer says 45 days, the source
says 30" is. It is also 20× faster, which is what lets Ring 0 run on everything.

**"How do you know the checker is right?"**
The Trust report, and the audit sample behind it — we spend part of the deep-check
budget re-checking answers we allowed, purely so misses can be counted.

**"Is the AI real?"**
Offline it is simulated, and deliberately flawed so the detectors have something
to catch. The detectors are real and run on the resulting text with no knowledge
of which scenario produced it. Add an API key and the same detectors run against
a real model — that is what the gateway is for.
