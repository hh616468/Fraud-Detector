# Detection results: test split, rules v0

Generated 2026-10-31T23:07:35 · rules fingerprint `10c60f31b5375467` · frozen: **True** (frozen at 2026-10-31T23:07:25)

400 held-out decisions, 100 scams and 300 legitimate. All data is synthetic; these numbers say nothing about production effectiveness.

## Headline

| Detector | Precision [95% CI] | Recall [95% CI] | FPR [95% CI] | TP | FP | TN | FN |
|---|---|---|---|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | 64.9% [56.4, 72.5] | 85.0% [76.7, 90.7] | 15.3% [11.7, 19.8] | 85 | 46 | 254 | 15 |
| Scorecard, coaching tier only (score ≥ 6 or override) | 85.4% [76.1, 91.4] | 70.0% [60.4, 78.1] | 4.0% [2.3, 6.9] | 70 | 12 | 288 | 30 |
| Baseline: warn on every new payee | 47.9% [40.5, 55.4] | 80.0% [71.1, 86.7] | 29.0% [24.2, 34.4] | 80 | 87 | 213 | 20 |

Intervals are Wilson score intervals. User-clustered bootstrap (1,000 resamples of users):

| Detector | Precision | Recall | FPR |
|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | [59.6, 71.5] | [76.5, 91.5] | [11.9, 18.4] |
| Scorecard, coaching tier only (score ≥ 6 or override) | [79.8, 92.4] | [60.5, 79.6] | [2.0, 5.9] |
| Baseline: warn on every new payee | [42.0, 54.3] | [71.9, 87.9] | [23.1, 35.0] |

## Against the slide targets (targets, not claims)

| Metric | Target | Measured (any warning) | Met? |
|---|---|---|---|
| precision | >= 90% | 64.9% | no |
| recall | >= 80% | 85.0% | yes |
| fpr | <= 2% | 15.3% | no |

## Recall by scam family

| Family | n | Held out | Any warning | Coaching tier | Baseline |
|---|---|---|---|---|---|
| exchange_deposit | 14 |  | 100.0% | 85.7% | 100.0% |
| hacked_relative | 14 |  | 64.3% | 50.0% | 64.3% |
| investment_platform | 15 | yes | 86.7% | 53.3% | 0.0% |
| marketplace_deposit | 15 | yes | 46.7% | 33.3% | 100.0% |
| prize_fee | 14 |  | 100.0% | 78.6% | 100.0% |
| refund_after_credit | 14 |  | 100.0% | 92.9% | 100.0% |
| safe_account | 14 |  | 100.0% | 100.0% | 100.0% |

## Warning rate on legitimate transfers, by type

| Category | n | Any warning | Coaching tier | Baseline |
|---|---|---|---|---|
| bill | 30 | 0.0% | 0.0% | 0.0% |
| cold_start | 39 | 2.6% | 0.0% | 41.0% |
| eid_gift | 10 | 20.0% | 0.0% | 40.0% |
| honest_call | 25 | 0.0% | 0.0% | 0.0% |
| honest_security_event | 30 | 6.7% | 3.3% | 6.7% |
| matched_twin | 30 | 86.7% | 33.3% | 100.0% |
| new_payee | 35 | 42.9% | 2.9% | 100.0% |
| routine | 81 | 0.0% | 0.0% | 0.0% |
| unusual_amount | 20 | 0.0% | 0.0% | 0.0% |

## Precision at assumed prevalence

The test set is enriched (25% scams), which flatters precision. Same recall and FPR at realistic rates:

| Detector | 0.1% | 0.5% | 1% | 5% |
|---|---|---|---|---|
| Scorecard, any warning (score ≥ 4 or override) | 0.6% | 2.7% | 5.3% | 22.6% |
| Scorecard, coaching tier only (score ≥ 6 or override) | 1.7% | 8.1% | 15.0% | 47.9% |
| Baseline: warn on every new payee | 0.3% | 1.4% | 2.7% | 12.7% |

Real Iraqi scam prevalence is unknown; this table is arithmetic, not an estimate.

## Rule firing rates

| Rule | Scams | Legitimate |
|---|---|---|
| R01 | 80.0% | 29.0% |
| R02 | 57.0% | 16.3% |
| R03 | 13.0% | 2.0% |
| R04 | 12.0% | 5.0% |
| R05 | 19.0% | 6.3% |
| R06 | 3.0% | 11.3% |
| R07 | 34.0% | 14.0% |
| R08 | 59.0% | 1.3% |
| R09 | 3.0% | 0.0% |
| R10 | 6.0% | 0.3% |
| R11 | 12.0% | 0.0% |
| R12 | 5.0% | 0.0% |
| R13 | 8.0% | 0.0% |
| M01 | 5.0% | 56.3% |
| M02 | 5.0% | 52.3% |

## Tiers

| Truth:tier | Count |
|---|---|
| legit:coach | 12 |
| legit:nudge | 34 |
| legit:pass | 254 |
| scam:coach | 70 |
| scam:nudge | 15 |
| scam:pass | 15 |

## Matched pairs (same user, time, amount, call flag; different intent)

| Outcome | Pairs |
|---|---|
| both warned | 24 |
| neither | 1 |
| scam only | 3 |
| twin only | 2 |

A transaction-only detector cannot separate pairs that look the same; this is its ceiling.

## Assessment latency

p50 0.39 ms · p95 0.59 ms · max 5.77 ms (scorecard only, in-process; the deck's budget is 500 ms for the warning).
