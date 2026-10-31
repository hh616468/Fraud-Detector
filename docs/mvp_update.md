# MVP update: Fraud & Scam Protection Agent

Team: Hussein Hamza & Sajjad Adil · rules v0 (frozen) · all data synthetic

## What works

- **The core flow runs end to end in a mock wallet:** a transfer is held, scored, warned about, coached,
  then cancelled or confirmed, committed exactly once, and logged. A user can try it at `/`, a judge can build any
  scenario at `/console`, and a fraud reviewer reads pseudonymised alerts at `/reviewer`.
- **Readable detector:** 13 risk rules and 2 credits in one YAML file, scored in under 1 ms. Every warning shows
  which rules fired and the values behind them.
- **Iraqi coaching that can't invent facts:**
  - The template warning appears instantly.
  - Gemma-4-31B then runs up to 3 tap-answer turns that name the specific concern, the scammer's likely next
    move and a safe check.
  - The model never sees names, notes or amounts. A validator blocks digits, unfired rules, unsupported scam
    stories, formal-Arabic phrasing and links.
  - In a live check, 30/30 turns passed.
- **Transaction safety, each property under test (70 tests):**
  - Cancel never debits.
  - Concurrent confirms commit once.
  - Edits invalidate consent.
  - Proceed is locked for 20 s at the top tier.
  - An LLM outage keeps the decision flow.
- **Honest held-out evaluation:**
  - 60 personas split by user (30/10/20).
  - 400 test decisions from a separate prompt set, including two scam families never used in tuning, matched
    twins and hard honest cases.
  - Rules were frozen before the single test run, and one command rebuilds the table.

## First numbers (held-out test, 100 scams / 300 legitimate)

| | Precision | Recall | False alarms |
|---|---|---|---|
| Scorecard, any warning | 64.9% | 85.0% | 15.3% |
| Scorecard, coaching tier | 85.4% | 70.0% | 4.0% |
| Flag every new payee | 47.9% | 80.0% | 29.0% |

## What doesn't work yet

- **Targets missed:** precision (≥ 90%) and false alarms (≤ 2%) are not met. Matched twins cause 26 of the 46
  false alarms, by design; honest large first payments cause most of the rest.
- **Weak scam types:**
  - Fake marketplace deposits (held out): 47% caught.
  - Hacked relatives using their own trusted account: 64% caught.
- **Coaching latency:** p50 4.8 s, against a 3 s target.
- **No human evaluation yet:** Iraqi wording has not been rated by native speakers.
- **Synthetic numbers only:** they cannot establish production effectiveness. At 1% prevalence, any-warning
  precision would be about 5%.

## What's next

1. Run the blind rater study: three Iraqi raters, contextual coaching vs an MSA notice. The packet is generated.
2. Add the Isolation Forest and LLM-as-detector baselines, then a fresh held-out set for the progress review.
3. Attack the weak spots with dev-only evidence: marketplace deposits (recipient age, deposit wording) and
   relative takeover (behaviour change on a trusted payee).
4. Reduce coaching latency: shorter turns, streaming, or a smaller model, measured against the 3 s budget.
5. Start the review loop: weekly labelled samples and AI-drafted rule edits, with a human approving each one
   and replaying it on fresh data.
