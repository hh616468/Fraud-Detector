# Fraud & Scam Protection Agent

Rule-based scam detection with Iraqi-Arabic LLM coaching, at the one moment we can still help: the transfer
screen, after the scammer's call and before the money moves.

A pending transfer in a mock wallet is held, scored by a readable scorecard, and, if risky, the user sees an
immediate Iraqi-Arabic warning followed by a short coaching chat. The user cancels or proceeds; the wallet
commits exactly once; every step is logged for a fraud reviewer. The LLM coaches only. It has no payment tool,
never decides whether to warn, and approves nothing.

> **Status: hackathon MVP.** All data is synthetic. Thresholds are starting values for validation, not fraud
> policy. Iraqi wording is a draft awaiting native-speaker review.

## How it works

```
web/ wallet · judge console · reviewer ──► FastAPI (wallet/api.py)
  1 draft held (pending, no money moves)
  2 as-of features from earlier transactions only        rules/features.py
  3 scorecard: 13 risk rules + 2 credits → score, tier    rules/engine.py, rules/catalogue.yaml
  4 template warning in the same response (<0.5 s)        coach/templates_iq.yaml
  5 coaching turn: Gemma → validator → or template        coach/session.py, coach/validator.py
  6 cancel (never debits) | confirm (PIN + draft version) wallet/service.py
  7 commit once + append-only, pseudonymised audit log    wallet/db.py
```

| Score | Tier | What the user sees |
|---|---|---|
| < 4 | pass | normal confirmation, logged |
| 4–5 | nudge | one-line Iraqi warning, Cancel / Proceed |
| ≥ 6, or (R06 or R07) + R03 | coach | warning + up to 3 tap-answer chat turns; Proceed unlocks after 20 s |

The full rule table, with Arabic and English text, is in [rules/catalogue.yaml](rules/catalogue.yaml).

### Safety properties (each has a test)

- **Commit exactly once:** `BEGIN IMMEDIATE` + a conditional UPDATE on `(status='pending', draft_version)`;
  idempotency keys on create and confirm; 8 concurrent confirms produce one debit.
- **Consent is bound to the draft:** editing amount, recipient, note or reason bumps `draft_version`,
  supersedes the assessment, and a confirm against the old version is rejected.
- **Cancel never debits; outages keep the flow:** LLM down, slow or invalid → reviewed template, decision
  flow unchanged.
- **The model can't invent facts:** it never sees recipient names, notes or amounts (the app fills
  `{amount}`/`{recipient}` placeholders). The validator rejects digits, citations of rules that did not fire,
  scam-story words the facts don't support (no prize talk without prize evidence), MSA phrasing and links.
- **No path from coach to wallet:** `coach/` cannot import `wallet/`; runtime code cannot read labels.

## Run it

Inside WSL Ubuntu (Python 3.12+):

```bash
./dev.sh setup          # venv at ~/.venvs/fraud-detector, installs the project
cp .env.example .env    # LLM endpoint, timeouts, DB path
./dev.sh seed           # three demo personas (add --generated dev for the 30 generated dev users)
./dev.sh run            # http://localhost:8000  (/console for judges, /reviewer for fraud reviewers)
```

Demo users share the PIN in `DEMO_PIN` (see [wallet/seed.py](wallet/seed.py)); it is a test value for this
mock wallet only.

The coach expects an OpenAI-compatible endpoint (`LLM_BASE_URL`, default `http://localhost:8070/v1`, model
`google/gemma-4-31B-it`). If WSL cannot reach a server that listens on Windows' 127.0.0.1, set
`networkingMode=mirrored` under `[wsl2]` in `%USERPROFILE%\.wslconfig` and run `wsl --shutdown`.

```bash
./dev.sh test           # unit, safety and architecture tests
./dev.sh smoke          # one live coaching turn with latency and validator verdict
```

## Data and evaluation

```bash
./dev.sh data --splits dev val       # personas → seeded histories → decisions; labels stored apart
./dev.sh validate --splits dev val   # code checks + data/generated/review_sample.md for manual review
./dev.sh eval --split dev            # tune on dev, check on val
./dev.sh freeze                      # record the rules fingerprint (commit + tag rules-v0)
./dev.sh data --splits test          # refuses unless frozen
./dev.sh eval --split test           # once
./dev.sh coach-eval --split val      # live coaching checks + blind rater packet
```

- 60 synthetic users, split **by user** 30 dev / 10 val / 20 test. Personas are written by the LLM (names,
  job, habits, contacts); a seeded Python simulator writes every number.
- Test: 400 decisions (100 scam / 300 legitimate) built with a different prompt set, including two scam
  families never used in tuning, small scams, honest unusual payments, cold starts, honest security events,
  honest calls and matched pairs. See [data/DATA_CARD.md](data/DATA_CARD.md).
- Results: [eval/results/](eval/results/). Every post-result change is in [docs/tuning_log.md](docs/tuning_log.md).
- Prevalence: the test set is 25% scams, which flatters precision. At 1% prevalence, 0.80 recall and 2% FPR give
  about 29% precision; the results include a prevalence table. Synthetic numbers cannot establish production
  effectiveness, and a cancel is a behaviour, not proof of fraud.

## Deviations from the slides

- **SQLite instead of PostgreSQL** (SQLAlchemy Core; a connection-string change).
- **Test set from a different prompt set and seed, same LLM**: only Gemma-4-31B was available locally.
- **Coaching latency:** Gemma-4-31B measured ~5 s per turn against a 3 s target. The template appears at once
  either way; `COACH_TIMEOUT_S` (default 8) sets how long the app waits for the model. Results report the share
  within 3 s.
- **R08/R09 for verified businesses:** the "many unrelated senders" clause and impersonation-by-name apply to
  personal accounts only (see the tuning log).

## Disclosure

- **Models:** `google/gemma-4-31B-it` (vLLM, OpenAI-compatible API, JSON mode, thinking off) writes personas,
  scam-script text and coaching turns. Prompts: [data/personas.py](data/personas.py),
  [data/scripts.py](data/scripts.py), [coach/prompts.py](coach/prompts.py).
- **Seeds:** simulator and decision builder use seed 2026 ([data/build.py](data/build.py)).
- **Catalogue sources:** Central Bank of Iraq warnings on impersonation of financial employees (18 Oct 2025)
  and fake exchange-company pages (17 Apr 2024); 964media on misuse of the Zain Cash name (15 Mar 2024).
- **AI coding assistant:** see the team's disclosure note.

Team: Hussein Hamza & Sajjad Adil.
