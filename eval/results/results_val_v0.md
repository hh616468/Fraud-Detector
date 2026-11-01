# Detection results: val split, rules v0

Generated 2026-10-31T23:07:02 · rules fingerprint `10c60f31b5375467` · frozen: **False**

200 held-out decisions, 50 scams and 150 legitimate. All data is synthetic; these numbers say nothing about production effectiveness.

## Headline

| Detector | Precision [95% CI] | Recall [95% CI] | FPR [95% CI] | TP | FP | TN | FN |
|---|---|---|---|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | 67.6% [55.8, 77.6] | 92.0% [81.2, 96.8] | 14.7% [9.9, 21.2] | 46 | 22 | 128 | 4 |
| Scorecard, coaching tier only (score ≥ 6 or override) | 81.2% [68.1, 89.8] | 78.0% [64.8, 87.2] | 6.0% [3.2, 11.0] | 39 | 9 | 141 | 11 |
| Baseline: warn on every new payee | 51.1% [41.0, 61.1] | 94.0% [83.8, 97.9] | 30.0% [23.2, 37.8] | 47 | 45 | 105 | 3 |

Intervals are Wilson score intervals. User-clustered bootstrap (1,000 resamples of users):

| Detector | Precision | Recall | FPR |
|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | [55.9, 75.6] | [82.7, 100.0] | [9.4, 24.7] |
| Scorecard, coaching tier only (score ≥ 6 or override) | [72.2, 90.2] | [67.9, 89.6] | [2.7, 10.8] |
| Baseline: warn on every new payee | [38.2, 59.8] | [84.0, 100.0] | [21.0, 46.2] |

## Against the slide targets (targets, not claims)

| Metric | Target | Measured (any warning) | Met? |
|---|---|---|---|
| precision | >= 90% | 67.6% | no |
| recall | >= 80% | 92.0% | yes |
| fpr | <= 2% | 14.7% | no |

## Recall by scam family

| Family | n | Held out | Any warning | Coaching tier | Baseline |
|---|---|---|---|---|---|
| exchange_deposit | 10 |  | 100.0% | 80.0% | 100.0% |
| hacked_relative | 10 |  | 60.0% | 40.0% | 70.0% |
| prize_fee | 10 |  | 100.0% | 80.0% | 100.0% |
| refund_after_credit | 10 |  | 100.0% | 90.0% | 100.0% |
| safe_account | 10 |  | 100.0% | 100.0% | 100.0% |

## Warning rate on legitimate transfers, by type

| Category | n | Any warning | Coaching tier | Baseline |
|---|---|---|---|---|
| bill | 15 | 0.0% | 0.0% | 0.0% |
| cold_start | 20 | 5.0% | 0.0% | 50.0% |
| eid_gift | 5 | 0.0% | 0.0% | 20.0% |
| honest_call | 12 | 0.0% | 0.0% | 0.0% |
| honest_security_event | 15 | 6.7% | 6.7% | 6.7% |
| matched_twin | 15 | 86.7% | 40.0% | 100.0% |
| new_payee | 18 | 38.9% | 11.1% | 100.0% |
| routine | 40 | 0.0% | 0.0% | 0.0% |
| unusual_amount | 10 | 0.0% | 0.0% | 0.0% |

## Precision at assumed prevalence

The test set is enriched (25% scams), which flatters precision. Same recall and FPR at realistic rates:

| Detector | 0.1% | 0.5% | 1% | 5% |
|---|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | 0.6% | 3.1% | 6.0% | 24.8% |
| Scorecard, coaching tier only (score ≥ 6 or override) | 1.3% | 6.1% | 11.6% | 40.6% |
| Baseline: warn on every new payee | 0.3% | 1.6% | 3.1% | 14.2% |

Real Iraqi scam prevalence is unknown; this table is arithmetic, not an estimate.

## Rule firing rates

| Rule | Scams | Legitimate |
|---|---|---|
| R01 | 94.0% | 30.0% |
| R02 | 48.0% | 15.3% |
| R03 | 22.0% | 4.0% |
| R04 | 0.0% | 1.3% |
| R05 | 20.0% | 7.3% |
| R06 | 6.0% | 10.7% |
| R07 | 48.0% | 14.0% |
| R08 | 62.0% | 2.7% |
| R09 | 6.0% | 0.0% |
| R10 | 8.0% | 0.7% |
| R11 | 18.0% | 0.0% |
| R12 | 10.0% | 0.0% |
| R13 | 14.0% | 0.0% |
| M01 | 4.0% | 54.7% |
| M02 | 6.0% | 56.0% |

## Tiers

| Truth:tier | Count |
|---|---|
| legit:coach | 9 |
| legit:nudge | 13 |
| legit:pass | 128 |
| scam:coach | 39 |
| scam:nudge | 7 |
| scam:pass | 4 |

## Matched pairs (same user, time, amount, call flag; different intent)

| Outcome | Pairs |
|---|---|
| both warned | 12 |
| scam only | 2 |
| twin only | 1 |

A transaction-only detector cannot separate pairs that look the same; this is its ceiling.

## Assessment latency

p50 0.37 ms · p95 0.65 ms · max 5.78 ms (scorecard only, in-process; the deck's budget is 500 ms for the warning).
