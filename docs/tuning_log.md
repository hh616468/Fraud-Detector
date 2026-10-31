# Tuning log

Every change made to the detector after looking at results, in order. Tuning uses the **dev** split
only; **val** is the check; **test** is touched once, after `python -m eval.freeze`.

## v0-a: first run (dev, offline personas)

- Scorecard (any warning): precision 59.4%, recall 90.7%, FPR 20.7%. Baseline (flag all new payees):
  50.2% / 92.7% / 30.7%.
- Largest false-alarm source: `R01 + R08` on honest first payments to shops, clinics, schools and
  charities. R08's "paid by many unrelated senders" clause fires on any popular merchant, because
  merchants are paid by strangers by design. The clause is meant to catch personal mule accounts.
- Second source: R09 on legitimate organisation names (e.g. a charity called "هيئة ...").
- Misses: mostly `hacked_relative` where the relative's own, long-trusted account was taken over
  (credits M01/M02 apply). The deck predicts this as the weakest family; left as an honest miss.

## v0-b: verified business accounts (dev → val)

- Change: added a provider-side `is_business` account attribute (verified merchant/organisation).
  R08's many-senders clause and R09 now apply only to personal accounts. Reported and
  under-7-days accounts still trigger R08 for everyone.
- Dev: precision 67.5%, recall 91.3%, FPR 14.7%. Val (not tuned on): 68.2% / 90.0% / 14.0%.
- Remaining dev false alarms: matched twins (by construction, they copy the scam's amount, time,
  call flag and device events) and `R01 + R02`: an honest, unusually large first payment, which
  the design deliberately nudges (one line, not the coaching chat).
- Decision: stop tuning. Further changes would fit the simulator rather than scams.

## v0-c: Gemma personas and scripts (dev → val), then freeze

- Rebuilt dev/val with LLM-written personas and script pools. Dev: 68.5% / 92.7% / 14.2%; val: 67.6% /
  92.0% / 14.7%. Same error patterns as v0-b, so no rule change.
- Froze rules v0 (fingerprint `10c60f31b5375467`, tag `rules-v0`), then built and evaluated the test split once:
  64.9% / 85.0% / 15.3%.

## Coaching prompt (val only; the detector is untouched)

- Live check on 30 val cases: 29/30 passed the validator; the one rejection mentioned a prize in an exchange case.
- Reading the passing turns found a grounding error the validator cannot see: given only R08's text ("reported,
  new, or paid by many unrelated senders"), the model claimed "many people sent money to this number" for an
  account that was merely new. The prompt now states the specific condition that held for each fired rule
  (`coach/prompts.py: describe`), without numbers. Re-run: 30/30 passed, and the case now says "a new number".

## Not changed on purpose

- Point values and tier thresholds stay as on slide 2.
- R02 floor (100,000 IQD) and R03 floor (50,000 IQD) were set before any run, to avoid calling
  small payments "unusually large" or a "drain".
