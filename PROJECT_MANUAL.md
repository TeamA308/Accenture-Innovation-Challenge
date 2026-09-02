# ControlPlane.ai — Project Manual

**Written for a non-expert reader.** Every technical word is explained the first
time it appears. If you only read one document about this project, read this one.

---

## 1. What this project does, in plain words

Companies now let AI write answers that people act on — replies to customers,
drafts for staff, recommendations inside decisions about money. Nobody is
checking what those answers actually say. If the AI invents a number, leaks
somebody's ID, or quietly treats two applicants differently, it usually gets
discovered weeks later by a complaint, an invoice or an audit.

**ControlPlane.ai sits between the AI and the action it triggers, and checks
every single answer before anyone acts on it.** It runs three layers of checking
— an instant one on everything, a deeper one on the small share of answers that
look doubtful, and a human reviewer for the rest — and it keeps a full record of
why it decided what it decided.

The key idea: **spend checking effort in proportion to risk.** An internal note
is not a payment instruction, and should not be policed like one.

---

## 2. Words you will meet, defined once

| Word | What it means here |
|---|---|
| **LLM** | Large Language Model. The AI that writes the answers (ChatGPT, Claude, and so on). |
| **Prompt** | The question or instruction sent to the AI. |
| **Token** | A chunk of text, roughly ¾ of a word. AI providers bill per token, so tokens are how cost is measured. |
| **PII** | Personally Identifiable Information — anything that identifies a real person: a name, a phone number, an ID number, a card number. |
| **Hallucination** | When the AI states something confidently that is simply not true. |
| **Grounding** | Checking whether each statement in an answer can actually be found in the source documents the answer was supposed to be based on. |
| **Ring 0 / Ring 1 / Ring 2** | Our three layers of checking. Explained in section 4. |
| **Verdict** | What we decided to do with an answer: allow, repair, flag, gate, or block. |
| **Policy** | The settings for one use case — how strict to be. Each use case has its own. |
| **Use case** | One way the company uses AI. We ship three: a customer support bot, an internal staff assistant, and a regulated decision tool. |
| **Reversible / irreversible** | Reversible means a mistake can be undone (a draft somebody reads). Irreversible means it cannot (a payment sent, an email delivered). |
| **WebSocket** | A connection the browser keeps open so the server can push updates the instant they happen, instead of the page constantly asking "anything new?". It is why the dashboard updates by itself. |
| **SSE (Server-Sent Events)** | A simpler one-way version of the same thing, used to stream the AI's words to the screen as they are written. |
| **API** | The set of web addresses the dashboard calls to get data. You can browse them all at `/docs` when the app is running. |
| **Checksum** | A maths rule built into an ID number that lets you verify it is real rather than a random string of digits. Card numbers, Aadhaar numbers and social security numbers all have one. |
| **False positive** | We flagged something that was actually fine. Annoying; causes people to ignore warnings. |
| **False negative** | We let through something that was actually bad. Dangerous. |

---

## 3. Map of the repository

Top level:

| Path | What lives there |
|---|---|
| `run.py` | **The one command that runs everything.** Sets up, builds, starts the server, opens your browser. |
| `start.bat` / `start.sh` | Double-clickable wrappers around `run.py` for Windows / Mac & Linux. |
| `README.md` | The public front page of the repo — what judges read first. |
| `PROJECT_MANUAL.md` | This document. |
| `.env.example` | A template of optional settings. You do **not** need one to run the project. |
| `backend/` | All the checking logic and the web server. Python. |
| `frontend/` | The dashboard you look at. TypeScript + React. |
| `seed/` | The demo prompts and the fake company documents used as "source of truth". |
| `docs/` | Architecture notes, the demo script, and the business proposal. |
| `data/` | Created on first run. Holds the local database file. Safe to delete to start fresh. |
| `.venv/` | Created on first run. The private Python environment. Never edit by hand. |
| `docker-compose.yml`, `backend/Dockerfile` | An optional container path (`docker compose up --build`). Provided for reviewers who prefer containers; `python run.py` is the path we test and demo on. |

### Inside `backend/`

| Path | What it is for |
|---|---|
| `requirements.txt` | The list of Python packages needed. Nine of them, all small. |
| `requirements-optional.txt` | Extras you do not need: real AI providers, Presidio, Postgres, Redis. |
| `app/main.py` | Starts the web server, seeds the three policies, starts the background worker, serves the dashboard. |
| `app/core/config.py` | All settings, each with a working default. |
| `app/core/orchestrator.py` | **The most important file.** The whole request journey in one place: stream the answer, check it, score it, save it, queue the deep check. |
| `app/core/llm_gateway.py` | One interface in front of every AI provider, so swapping models changes nothing else. |
| `app/core/providers/mock.py` | The offline simulated AI. Lets the whole thing run with no internet and no API key. |
| `app/core/providers/remote.py` | Real OpenAI and Anthropic adapters. Only used if you supply a key. |
| `app/core/providers/pricing.py` | Per-model prices, so every answer carries a cost. |
| `app/core/bus.py` | Delivers live updates to the dashboard and queues deep-check jobs. |
| `app/core/telemetry.py` | Measures how fast the checks run and what a normal answer costs. |
| `app/rings/ring0/` | The instant checks. One file per check — see section 4. |
| `app/rings/ring1/` | The deep checks: second-opinion model, claim-by-claim verification, bias twin, and the spend budget. |
| `app/rings/ring2/` | The human loop: the threshold tuner and the trust report. |
| `app/api/` | The web addresses: `generate.py`, `policy.py`, `review.py`, `audit.py`, `metrics.py`, `demo.py`. |
| `app/models/` | The database tables: responses, policies, overrides, conversations. |
| `app/db/session.py` | Database connection. SQLite by default, Postgres if you point it at one. |
| `tests/` | 71 automated tests, plus two rehearsal scripts (`smoke.py`, `demo_loop_check.py`). |

### Inside `frontend/`

| Path | What it is |
|---|---|
| `src/App.tsx` | The sidebar and which page is showing. |
| `src/pages/Dashboard.tsx` | The main live screen. |
| `src/pages/Evidence.tsx` | The full record behind one answer. |
| `src/pages/ReviewQueue.tsx` | The human review list. |
| `src/pages/BiasMirror.tsx` | The side-by-side bias tool. |
| `src/pages/Policies.tsx` | The sliders that change how strict each use case is. |
| `src/pages/Trust.tsx` | How well the checker itself is performing. |
| `src/components/` | Reusable pieces: the coloured verdict badge, the signal panel, the bias diff. |
| `src/lib/api.ts` | Every call the dashboard makes to the backend. |
| `src/lib/ws.ts` | The live-update connection. |
| `src/styles.css` | All the visual design, in one file. |
| `dist/` | The built dashboard. Committed to the repo so a fresh clone needs no Node.js. |

---

## 4. The pieces, and what happens when you touch them

### Ring 0 — the instant check (runs on 100% of answers)

**What it does.** The moment the AI produces an answer, six checks run on it.
Together they take between one and four **thousandths of a second**, and they
make no second AI call — they read what the first one already produced.

| Check | Question it answers | File |
|---|---|---|
| Personal data | Does this contain someone's ID, card, phone, email, address? | `ring0/pii.py` |
| Credentials | Does this contain an API key, password or access token? | `ring0/secrets.py` |
| Arithmetic | If the answer shows its working, does the maths add up? | `ring0/schema_check.py` |
| Uncertainty | How sure was the AI about its own words? | `ring0/uncertainty.py` |
| Grounding | Can each claim be found in the source documents? | `ring0/grounding.py` |
| Cost | Did this cost far more than this kind of question usually does? | `ring0/cost.py` |

**Why it exists.** Because checking after the fact is not a control. If a leaked
card number reaches the customer and *then* gets flagged, nothing was prevented.

**Two things worth knowing:**

1. **It can cut the answer off mid-sentence.** While the AI is still typing, we
   re-scan the text every few words. If an ID number or an API key appears, we
   stop generation immediately. On screen you will see the answer stop
   half-way through a word.

2. **ID numbers are verified, not guessed at.** A card number is checked against
   the Luhn checksum, an Aadhaar number against the Verhoeff checksum, a US
   social security number against the rules governing which numbers were ever
   issued. A random 16-digit invoice reference is therefore *not* flagged as a
   card. This matters: it is the difference between a guess and evidence.

**What happens if you change it.** The thresholds live in the Policy console
(section 4.4), not in the code. Loosening the grounding slider means fewer
answers get flagged for being unverifiable; tightening it means more.

### Ring 1 — the deep check (runs on roughly 5–12% of answers)

**What it does.** For answers that landed in the grey zone, three deeper checks
run *in the background*, after the user already has their answer:

- **Second opinion.** A smaller, cheaper AI is asked to work the question out
  from the source documents itself, then compare. It is not asked "is this
  good?" — that just gets agreement. It is asked "what is the answer?".
- **Claim-by-claim verification.** The answer is broken into individual
  statements and each is matched against the sources, so one true half of a
  sentence can no longer carry a false half through.
- **The bias twin.** The question is asked again with exactly one attribute
  changed — the applicant's name, a pronoun, an age band — and the two answers
  are compared. See section 4.3.

**Why it exists.** These checks cost real money and take about half a second.
Running them on every answer would be wasteful; running them on none would mean
the grey zone never gets resolved.

**The budget is real code, not a claim.** `ring1/budget.py` refuses to start a
deep check once the use case has spent its allowance — either its share of
traffic or its share of spend. Work that does not fit is marked "deferred"
and shown as such, rather than quietly dropped. Answers feeding an
**irreversible** action skip the traffic cap, because deferring that check to
save a fraction of a cent is the wrong trade.

**What happens if you change it.** On the Policy page, raising "deep-check
volume cap" sends more traffic to Ring 1 (more thorough, more expensive).
Raising "oversight spend cap" raises the money ceiling.

### Ring 2 — the human loop

**What it does.** Everything the machine would not resolve goes into a review
queue, sorted by risk rather than by arrival. A reviewer picks one of three
buttons:

| Button | What it means | What the system learns |
|---|---|---|
| **Accept — we over-flagged** | The answer was fine | A false alarm on that signal |
| **Reject — the flag was right** | The answer really was bad | The flag was correct |
| **Edit and release** | Partly right, a human fixed it | Partly correct |

**What happens when you click.** Immediately: the answer is released, blocked,
or replaced with the edit, and a permanent record is written. Then the tuner in
`ring2/threshold_tuner.py` looks at the last five reviewer decisions on that
same signal for that same use case. If more than 30% say we over-flagged, it
loosens that threshold by one small step and writes down exactly why — you can
read the sentence on the Policy page.

**Two rules that matter:**

- **A single miss tightens immediately.** If a reviewer rejects something we
  *allowed*, that is a false negative and the threshold tightens at once. Missing
  a real problem and raising a false alarm are not equally bad, and the system
  does not pretend they are.
- **Some things are never tuned away.** A card number that passes its checksum
  is a card number. No number of reviewer disagreements will relax a personal-data
  block or the arithmetic checker. Only *probabilistic* thresholds move.

### 4.3 The Bias mirror

**What it does.** You type a question about a person. It asks the AI twice —
once as written, once with one attribute swapped (Priya → Rohan, she → he,
28-year-old → 58-year-old) — and shows both answers side by side.

**Why it exists.** Bias never shows up in one answer. Read a single loan
recommendation and it reads like reasoning. You only see the problem when you
ask about two people who differ in one irrelevant way.

**What it compares.** Not just how similar the wording is. It pulls out the
parts of an answer that carry consequences — the verdict (approve / decline),
the amounts, the interest rate, and the conditions attached (guarantor,
collateral, co-applicant) — and diffs those directly. "Same facts, different
name: limit dropped from 12,00,000 to 7,00,000 and a guarantor was demanded" is
a finding someone can act on. "Similarity 0.73" is not.

**An honesty note.** In offline demo mode the *simulated AI* is deliberately
biased, so the probe has something to find. The bias is a property of the model
being watched, not of the checker. Plug in a real API key and the identical
detector runs against a real model.

### 4.4 The Policy console

**What it does.** Three sets of sliders, one per use case. Every number Ring 0
and Ring 1 use is read from here at the moment a request arrives.

**What happens when you move a slider and press Save.** The very next request
through that use case behaves differently. This is the demonstration that
"risk-adaptive" is real: send the same prompt as a customer-support answer and
as an internal note and you get different verdicts from identical signals.

The chart underneath shows every time a threshold changed — by a human on this
page, or by the learning loop — with the reason attached.

### 4.5 The dashboard

The main screen. Type a prompt, pick which use case's rules apply, choose
whether the answer feeds a reversible draft or an irreversible action, and send.
You will see:

- the answer streaming in word by word,
- a coloured verdict badge landing a beat later,
- a spinner if a deep check is running, replaced by its result when it lands,
- the reasons, in plain sentences,
- a live table of recent traffic that updates by itself.

The four tiles across the top are measured, not decorative: median instant-check
speed, share of traffic sent for deep review, oversight cost as a percentage of
what the AI itself cost, and recoverable spend the cost lane has spotted.

**"Replay 120 requests"** pushes synthetic traffic through the real pipeline so
the charts have something in them. **"Clear traffic"** empties the history and
leaves the policies alone.

### 4.6 The Trust report

Answers the question that ends most demos of this kind of tool: *how do you know
your checker is any good?*

Every reviewer decision is a labelled example, which gives precision (of what we
flagged, how much was really a problem) and false-positive rate. Misses are
harder — nobody reviews what you let through — so a slice of the deep-check
budget is deliberately spent re-checking answers Ring 0 **allowed**, purely so
that misses can be counted rather than assumed to be zero.

Every number is shown with its sample size, and the page says plainly when the
sample is too small to conclude anything.

### 4.7 The verdicts

| Verdict | Colour | What actually happens |
|---|---|---|
| **allow** | green | Delivered unchanged. |
| **repair** (`edit`) | yellow | Delivered with a correction attached. Only mechanical errors — a wrong sum — are corrected. The substance is never rewritten. |
| **flag** | orange | Delivered in full, annotated, and queued for review. A probability never blocks a reversible answer. |
| **gate commit** | blue | The person sees every word. What is held is the *action* — the payment, the email, the record write — until a deep check or a human clears it. |
| **block** | red | Withheld and redacted. Only for certainties: verified personal data or a credential. |

The rule behind the table: **certainty decides the kind of response,
reversibility decides the severity.**

---

## 5. Running it from a clean copy

### What you need

- **Python 3.10 or newer.** Check with `python --version`.
- Nothing else. No database, no Docker, no API key, no internet after cloning.
- (Optional) Node.js, only if you want to change the dashboard's code.

### The steps

```bash
git clone <the repository url>
cd Project
python run.py
```

On Windows you can double-click **`start.bat`** instead.

### What you should see

```
==========================================================
  ControlPlane.ai - risk-adaptive oversight for LLMs
  Team A308 | Accenture Innovation Challenge 2026
==========================================================

> Creating a private Python environment (.venv)
  done

> Installing dependencies (about a minute the first time)
  done

> Starting
  dashboard   http://127.0.0.1:8000
  API docs    http://127.0.0.1:8000/docs
  press Ctrl+C to stop

  seeded 3 default policies
  backfilling 240 synthetic interactions...
  backfill complete
  ControlPlane.ai ready on http://127.0.0.1:8000
```

Your browser opens by itself after a few seconds. You should see a dark purple
dashboard with four number tiles across the top, a prompt box on the left, a
chart on the right, and a table of recent responses at the bottom with a few
hundred rows already in it.

First run takes about 90 seconds — most of it installing packages and generating
the sample traffic. Every run after that takes about 15 seconds.

Press **Ctrl+C** in the terminal to stop.

### Useful variations

| Command | What it does |
|---|---|
| `python run.py --no-seed` | Start with an empty dashboard. |
| `python run.py --port 9000` | Use a specific port. |
| `python run.py --no-browser` | Do not open a browser. |
| `python run.py --dev` | Restart the server automatically when you edit backend code. |
| `python run.py --rebuild-ui` | Rebuild the dashboard (needs Node.js). |

### Checking it works, without clicking

```bash
# 71 automated tests, about five seconds
.venv/Scripts/python -m pytest backend        # Windows
.venv/bin/python -m pytest backend            # Mac / Linux

# every demo scenario, end to end against the running server
.venv/Scripts/python backend/tests/smoke.py http://127.0.0.1:8000

# rehearse the learning-loop moment
.venv/Scripts/python backend/tests/demo_loop_check.py http://127.0.0.1:8000
```

### Using a real AI model instead of the simulator

Copy `.env.example` to `.env`, put a key in `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`, restart. The provider dropdown on the dashboard lights up.
Nothing else changes — same checks, same thresholds, same records. That is the
point of the design.

---

## 6. The demo, step by step

About four minutes. Rehearse it once with `smoke.py` first. The same script is
in `docs/demo_script.md` with timings.

### Before you start

1. `python run.py` and wait for the browser.
2. Confirm the dashboard has data in the bottom table. If not, click
   **Replay 120 requests**.
3. Stay on **Live oversight**.

### Beat 1 — a leak, stopped mid-sentence (45 seconds)

1. In **Try a demo prompt**, choose **"Personal data and a live credential leak"**.
2. Press **Send**.
3. Watch the answer start writing out a customer record — then **stop
   mid-sentence**.

> "The model was still typing when we cut it off. That is a customer's Aadhaar
> number, a card number and a live API key. We did not detect this afterwards —
> we stopped it happening."

4. Point at the verdict: **block**, red. Read one reason aloud — note the phrase
   *validated by verhoeff*.

> "This is not a regex that saw twelve digits. It ran the Aadhaar checksum. A
> random twelve-digit reference number would not be touched. That distinction is
> what makes this evidence rather than a guess."

5. Point at the instant-check speed tile: **about 1.5 thousandths of a second**.

> "That runs on one hundred percent of traffic and makes no second AI call."

### Beat 2 — confidently wrong, caught with a citation (60 seconds)

1. Choose **"Confidently wrong against the source policy"**. Send.
2. The answer arrives fluently and completely. Verdict: **flag**, orange.
3. Read the reason:

> "It says the refund window is 45 days. The policy document we gave it says 30.
> Not 'this looks uncertain' — the system can point at the sentence it
> contradicts."

4. Point at the spinner: **ring 1 running**. Wait for it to resolve (~1 second).

> "The user already has their answer. The deep check happens beside them. The
> second-opinion model just re-derived it from the source and disagrees."

5. Click **Open evidence →**. Show the **Ring 0 signals** tab: the claims list,
   green for supported, red for contradicted, each with the source sentence
   underneath.

> "This is the record a data-protection or EU AI Act reviewer asks for. Every
> signal, its score, the exact characters it fired on, and the source text."

### Beat 3 — the same prompt, two policies (30 seconds)

1. Back to **Live oversight**. Choose **"Policy contrast, run 1 of 2"**. Send —
   note the verdict and confidence.
2. Choose **"Policy contrast, run 2 of 2"**. Send.

> "Identical prompt, identical answer, identical signals. Different use case,
> different verdict. The regulated policy holds it and sends it for a deep check;
> the internal one lets it through with a note. That is the whole product: one
> checker, several risk appetites."

### Beat 4 — bias you can see (45 seconds)

1. Click **Bias mirror** in the sidebar. The loan question is pre-filled.
2. Click **Run the twin**.

> "Same income, same employment history, same repayment record. One name changed."

3. Point at the two answers side by side, then at **What actually changed**:

> "Approved in full at 10.5 percent for Rohan. Seven lakh instead of twelve at
> 13.25 percent, with a guarantor demanded, for Priya. You would never see this
> reading one answer at a time — and reading one answer at a time is exactly what
> every review process does."

4. Scroll to the attribute list.

> "Deliberately a readable list, not a learned model. Anyone can inspect exactly
> which swaps we make and challenge them. A bias probe nobody can audit is not
> evidence of anything."

### Beat 5 — gating an irreversible action (30 seconds)

1. **Live oversight** → **"Irreversible action: a vendor payment"**. Send.
2. Verdict: **gate commit**, blue, with **commit held**.

> "The person can read every word. What is held is the payment. The answer never
> waits — only the action does. If this had been a draft, the same signals would
> have produced a note, not a hold."

### Beat 6 — the system learns, live (60 seconds)

1. Click **Policy console** → **Internal knowledge copilot**. Note
   **Grounding floor** (0.45) and the empty history chart.
2. Click **Review queue**. Expand the first item, read why it was flagged.
3. Click **Accept — we over-flagged** on five items in a row. Watch the banner
   after each: *"recorded; not enough evidence to move a threshold yet"*.

> "One reviewer's opinion is not evidence. It wants a run."

4. On the fifth, the banner changes: **threshold moved**. Read the reason aloud.
5. Go back to **Policy console**. The slider has moved to 0.40 and the chart has
   a step in it.

> "Five human decisions, one threshold change, and a written reason attached.
> That is the feedback loop, running."

6. Point at the blocked-entity list below.

> "And note what did *not* move. A validated card number is not up for
> negotiation, however many times a reviewer disagrees."

### Beat 7 — the cost lane and the honesty page (30 seconds)

1. **Live oversight**: point at **Recoverable spend** and **Oversight budget**.

> "Oversight is costing about three percent of what the AI itself costs, capped
> in code — you can read the admission controller. And the cost lane has already
> spotted more recoverable spend than the checking costs. It pays for itself."

2. Click **Trust report**.

> "And this is the one nobody shows you. Of what we flagged, how much was really
> a problem. How many we missed — measured, by deliberately re-checking answers
> we allowed, because otherwise we would only ever count our own successes.
> Every number with its sample size, and it tells you when the sample is too
> small to trust."

### Closing line

> "Every answer carries a verdict, a reason, and a record. The rules differ by
> risk. The humans teach it. And it tells you honestly how well it is doing."

---

## 7. If something breaks

Most likely, in order:

**The browser shows "cannot connect" or the page never loads.**
Look at the terminal. If it says a port was busy, it will have picked another
one — read the `dashboard` line for the real address. If the terminal shows a
Python error, jump to the last item below.

**The dashboard loads but every number is zero and the table is empty.**
The sample traffic did not generate. Click **Replay 120 requests** on the
dashboard. If that fails, check the terminal for a `backfill failed` line.

**"Port 8000 was busy, using 8001 instead."**
Normal and handled. Something else on your machine is using port 8000. Use the
address printed in the terminal. To force one: `python run.py --port 9000`.

**The verdict badge never appears / the answer never finishes.**
The connection dropped mid-stream. Reload the page and send again. The top-right
of the composer card shows `live` or `reconnecting`.

**The deep check says "deferred" instead of running.**
Not a bug — the budget is full. That is the spend cap doing its job. To see Ring
1 fire anyway, either click **Clear traffic** and send the prompt again, or raise
"deep-check volume cap" on the Policy page.

**Overriding items in the review queue does not move any threshold.**
The tuner needs **five** decisions on the **same signal** for the **same use
case** before it moves anything, and it will never move a deterministic check.
Expand each queue item and pick ones whose reason mentions *grounding coverage*.
`backend/tests/demo_loop_check.py` does this automatically.

**Everything is stale, or the data looks wrong.**
Stop the server (Ctrl+C), delete the `data/` folder, start again. That resets
all traffic and policies to a clean state.

**A Python error in the terminal on startup.**
Check `python --version` is 3.10 or newer. Then delete the `.venv` folder and run
`python run.py` again — it rebuilds the environment from scratch.

**"Node.js not found and no pre-built dashboard present."**
Only appears if `frontend/dist/` is missing. The built dashboard is committed to
the repo, so this should not happen. If it does, install Node.js and run
`python run.py --rebuild-ui`.

**Nothing above helped.**
`python run.py --no-seed` starts the simplest possible version. If that works,
the problem is in the sample data generation, not the system.

---

## 8. Honest limitations

Worth knowing before somebody asks:

- **The offline model is simulated.** It produces realistic enterprise answers,
  including deliberately flawed ones, and emits real per-word confidence numbers.
  The *detectors* are real and run on the resulting text with no knowledge of
  which scenario produced it. Supply an API key and they run unchanged against a
  real model.
- **The grounding checker is English-only** and works on text documents. Other
  languages, tables and images are out of scope for a prototype.
- **The protected-attribute list is small and hand-written.** That is deliberate
  — auditability over coverage — but it means the bias probe only fires on
  attributes we listed.
- **The trust numbers need volume.** With a handful of reviewer decisions the
  precision figure is noise, and the page says so.
- **Deterministic checks catch what has a rule.** A hallucinated claim with no
  numbers in it, against no source document, is genuinely hard — which is why the
  system reports "unverifiable" rather than pretending otherwise.
