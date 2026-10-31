# Detection results: dev split, rules v0

Generated 2026-10-31T23:07:02 · rules fingerprint `10c60f31b5375467` · frozen: **False**

600 held-out decisions, 150 scams and 450 legitimate. All data is synthetic; these numbers say nothing about production effectiveness.

## Headline

| Detector | Precision [95% CI] | Recall [95% CI] | FPR [95% CI] | TP | FP | TN | FN |
|---|---|---|---|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | 68.5% [61.8, 74.5] | 92.7% [87.3, 95.9] | 14.2% [11.3, 17.8] | 139 | 64 | 386 | 11 |
| Scorecard, coaching tier only (score ≥ 6 or override) | 84.8% [78.1, 89.8] | 82.0% [75.1, 87.3] | 4.9% [3.3, 7.3] | 123 | 22 | 428 | 27 |
| Baseline: warn on every new payee | 50.7% [44.9, 56.5] | 94.7% [89.8, 97.3] | 30.7% [26.6, 35.1] | 142 | 138 | 312 | 8 |

Intervals are Wilson score intervals. User-clustered bootstrap (1,000 resamples of users):

| Detector | Precision | Recall | FPR |
|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | [62.5, 74.4] | [87.6, 97.2] | [11.0, 18.0] |
| Scorecard, coaching tier only (score ≥ 6 or override) | [78.9, 90.3] | [76.4, 87.7] | [3.0, 7.1] |
| Baseline: warn on every new payee | [45.2, 55.9] | [90.3, 98.7] | [25.4, 36.7] |

## Against the slide targets (targets, not claims)

| Metric | Target | Measured (any warning) | Met? |
|---|---|---|---|
| precision | >= 90% | 68.5% | no |
| recall | >= 80% | 92.7% | yes |
| fpr | <= 2% | 14.2% | no |

## Recall by scam family

| Family | n | Held out | Any warning | Coaching tier | Baseline |
|---|---|---|---|---|---|
| exchange_deposit | 30 |  | 100.0% | 83.3% | 100.0% |
| hacked_relative | 30 |  | 70.0% | 56.7% | 73.3% |
| prize_fee | 30 |  | 96.7% | 83.3% | 100.0% |
| refund_after_credit | 30 |  | 96.7% | 90.0% | 100.0% |
| safe_account | 30 |  | 100.0% | 96.7% | 100.0% |

## Warning rate on legitimate transfers, by type

| Category | n | Any warning | Coaching tier | Baseline |
|---|---|---|---|---|
| bill | 45 | 0.0% | 0.0% | 2.2% |
| cold_start | 60 | 5.0% | 1.7% | 53.3% |
| eid_gift | 15 | 6.7% | 0.0% | 33.3% |
| honest_call | 38 | 2.6% | 2.6% | 0.0% |
| honest_security_event | 45 | 8.9% | 8.9% | 6.7% |
| matched_twin | 45 | 77.8% | 31.1% | 100.0% |
| new_payee | 52 | 36.5% | 3.8% | 100.0% |
| routine | 120 | 0.0% | 0.0% | 0.0% |
| unusual_amount | 30 | 3.3% | 0.0% | 0.0% |

## Precision at assumed prevalence

The test set is enriched (25% scams), which flatters precision. Same recall and FPR at realistic rates:

| Detector | 0.1% | 0.5% | 1% | 5% |
|---|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | 0.6% | 3.2% | 6.2% | 25.5% |
| Scorecard, coaching tier only (score ≥ 6 or override) | 1.7% | 7.8% | 14.5% | 46.9% |
| Baseline: warn on every new payee | 0.3% | 1.5% | 3.0% | 14.0% |

Real Iraqi scam prevalence is unknown; this table is arithmetic, not an estimate.

## Rule firing rates

| Rule | Scams | Legitimate |
|---|---|---|
| R01 | 94.7% | 30.7% |
| R02 | 56.7% | 11.6% |
| R03 | 19.3% | 2.2% |
| R04 | 0.0% | 2.9% |
| R05 | 21.3% | 7.8% |
| R06 | 4.7% | 10.2% |
| R07 | 40.0% | 13.1% |
| R08 | 62.7% | 3.6% |
| R09 | 7.3% | 0.0% |
| R10 | 9.3% | 1.8% |
| R11 | 16.0% | 0.0% |
| R12 | 11.3% | 0.9% |
| R13 | 18.0% | 0.0% |
| M01 | 4.7% | 54.0% |
| M02 | 5.3% | 48.0% |

## Tiers

| Truth:tier | Count |
|---|---|
| legit:coach | 22 |
| legit:nudge | 42 |
| legit:pass | 386 |
| scam:coach | 123 |
| scam:nudge | 16 |
| scam:pass | 11 |

## Matched pairs (same user, time, amount, call flag; different intent)

| Outcome | Pairs |
|---|---|
| both warned | 33 |
| neither | 1 |
| scam only | 9 |
| twin only | 2 |

A transaction-only detector cannot separate pairs that look the same; this is its ceiling.

## Assessment latency

p50 0.39 ms · p95 0.65 ms · max 5.98 ms (scorecard only, in-process; the deck's budget is 500 ms for the warning).
