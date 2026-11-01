# Data card: synthetic Iraqi wallet users

All data is synthetic. No real person, account or transaction is used at any stage.

## Users

| Split | Users | Retiree | Salaried | First-time (cold start) | Persona prompt set |
|---|---|---|---|---|---|
| dev | 30 | 10 | 12 | 8 | A |
| val | 10 | 3 | 4 | 3 | A |
| test | 20 | 7 | 7 | 6 | B |

- Python fixes each persona slot's segment, city, gender, age band, income band and active hours. The LLM
  writes names, job, habits (from a fixed vocabulary) and contacts; it writes no numbers.
- `simulator.py` (seed 2026) writes about 300 days of history ending 25 Oct 2026 (14–70 days for first-time
  users): salary or pension on a pay day, cash-ins, rent, generator fees, savings-group instalments, bills,
  top-ups, family transfers, shops (some verified business accounts, some personal), clinics, school fees,
  donations, Ramadan hour shifts and Eid gift bursts (including relatives who are not saved contacts), plus honest
  new-device, PIN-reset and SIM-change events. Amounts are IQD rounded to 250; balances never go negative.

## Decisions

Each decision is a pending transfer at a timestamp, scored against the user's history strictly before it plus
the decision's own setup (e.g. a stranger's credit, a device event). Decisions never see each other.

| Legitimate category (per 20 users) | n | What it tests |
|---|---|---|
| routine | 80 | familiar recipients, usual amounts |
| bill | 30 | billers and top-ups |
| new_payee | 35 | honest first payments (shops, clinics, relatives) |
| unusual_amount | 20 | honest spikes to familiar recipients |
| eid_gift | 10 | Eid bursts, sometimes to unsaved relatives |
| cold_start | 40 | users with fewer than 10 past transfers |
| honest_security_event | 30 | normal transfer hours after a new phone / PIN reset / SIM swap |
| honest_call | 25 | paying family while on the phone with them |
| matched_twin | 30 | same user, time, amount, call flag and device events as a scam; honest recipient |

Dev and val scale these by user count (×1.5, ×0.5); a short category is topped up with routine transfers.

| Scam family | dev / val | test | Built from |
|---|---|---|---|
| safe_account | 30 / 10 | 14 | CBI impersonation warning (Oct 2025) |
| exchange_deposit | 30 / 10 | 14 | CBI fake exchange pages warning (Apr 2024) |
| prize_fee | 30 / 10 | 14 | 964media, Zain Cash name misuse (Mar 2024) |
| refund_after_credit | 30 / 10 | 14 | team catalogue |
| hacked_relative | 30 / 10 | 14 | team catalogue |
| marketplace_deposit | – | 15 | **held out**, never used in tuning |
| investment_platform | – | 15 | **held out**, never used in tuning |

Scam scripts (recipient display names and notes) come from the LLM, set A for dev/val and set B for test.
Set B is asked to avoid the obvious keywords in about half of its notes. Python sets amounts, timing, call flags,
account age and mule signals. Some scams are deliberately hard: small prize fees, refunds sent to a different
wallet, and hacked relatives using the relative's own trusted account.

## Separation

- Labels are written to `data/generated/labels/<split>.jsonl`; decision records carry no label fields
  (checked by `data/validate.py`), and runtime packages cannot reference the label store (architecture test).
- The test split can only be built and evaluated after `python -m eval.freeze`.

## Known limits

- One LLM for both prompt sets; the test set differs by prompt, temperature and seed, not by model.
- Gemma's persona names collapse onto a few surnames (الخفاجي, الجبوري) and it rarely picks some habits
  (savings groups, bills). Names do not drive any rule except R12 via contact names; Python writes every number.
- "Unrelated senders", call state and account type are simulated provider-side signals.
- The simulator encodes our assumptions about Iraqi wallet use; results measure the detector against those
  assumptions, not against real fraud.
