"""Historical features for one pending transfer.

Only transactions and events strictly before the draft's timestamp are used, so a feature can
never peek at the transfer it is scoring or anything after it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from rules.types import Contact, DeviceEvent, DraftContext, Txn

INCOME_KINDS = {"salary", "pension", "cash_in"}


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100), same convention as numpy's default."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


@dataclass(frozen=True)
class Features:
    ts: datetime
    amount: int
    balance_before: int
    n_prior_out: int
    amount_median: float | None
    amount_mad: float | None
    amount_p95: float | None
    prior_to_recipient: tuple[Txn, ...]
    recipient_first_seen: datetime | None
    prior_out_ts: tuple[datetime, ...]
    hour_counts: tuple[int, ...]
    saved_contact: Contact | None
    device_events: tuple[DeviceEvent, ...]
    stranger_credits: tuple[Txn, ...]
    recipient_age_days: float | None

    @property
    def is_new_recipient(self) -> bool:
        return not self.prior_to_recipient


def compute_features(ctx: DraftContext) -> Features:
    d = ctx.draft
    before = [t for t in ctx.history if t.ts < d.ts]
    prior_out = sorted((t for t in before if t.direction == "out"), key=lambda t: t.ts)
    amounts = [float(t.amount) for t in prior_out]

    median = mad = p95 = None
    if amounts:
        median = statistics.median(amounts)
        mad = statistics.median(abs(a - median) for a in amounts)
        p95 = percentile(amounts, 95)

    to_recipient = tuple(t for t in prior_out if t.counterparty == d.recipient_wallet)

    hours = [0] * 24
    for t in prior_out:
        hours[t.ts.hour] += 1

    contact = next((c for c in ctx.contacts if c.wallet == d.recipient_wallet and c.saved_at <= d.ts), None)
    contact_wallets = {c.wallet for c in ctx.contacts if c.saved_at <= d.ts}

    # Incoming person-to-person credits in the last day from wallets the user never paid and
    # has not saved: the setup for a refund scam.
    day_ago = d.ts - timedelta(hours=24)
    stranger_credits = []
    for t in before:
        if t.direction != "in" or t.kind in INCOME_KINDS or t.ts < day_ago:
            continue
        if t.counterparty in contact_wallets:
            continue
        if any(o.counterparty == t.counterparty and o.ts < t.ts for o in prior_out):
            continue
        stranger_credits.append(t)

    age_days = None
    if ctx.recipient.created_at is not None:
        age_days = (d.ts - ctx.recipient.created_at).total_seconds() / 86400

    return Features(
        ts=d.ts,
        amount=d.amount,
        balance_before=ctx.balance,
        n_prior_out=len(prior_out),
        amount_median=median,
        amount_mad=mad,
        amount_p95=p95,
        prior_to_recipient=to_recipient,
        recipient_first_seen=to_recipient[0].ts if to_recipient else None,
        prior_out_ts=tuple(t.ts for t in prior_out),
        hour_counts=tuple(hours),
        saved_contact=contact,
        device_events=tuple(e for e in ctx.device_events if e.ts < d.ts),
        stranger_credits=tuple(sorted(stranger_credits, key=lambda t: t.ts)),
        recipient_age_days=age_days,
    )
