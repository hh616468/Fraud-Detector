"""The scorecard: YAML catalogue + one small function per rule.

`Scorecard.assess` returns the score, the tier, every rule's verdict and the values behind it,
so the warning, the coach and the reviewer all read the same trace.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rules import lexicons
from rules.calendar import in_ramadan
from rules.features import Features, compute_features
from rules.types import DraftContext

RULES_DIR = Path(__file__).resolve().parent
CATALOGUE_PATH = RULES_DIR / "catalogue.yaml"
# Files whose content defines detector behaviour; their hash is the frozen rules version.
FINGERPRINT_FILES = ("catalogue.yaml", "lexicons.py", "features.py", "engine.py", "calendar.py")

RuleFn = Callable[[DraftContext, Features, dict], tuple[bool, dict]]
_RULES: dict[str, RuleFn] = {}


def rule(rule_id: str):
    def register(fn: RuleFn) -> RuleFn:
        _RULES[rule_id] = fn
        return fn

    return register


def _round(value: float | None, ndigits: int = 2) -> float | None:
    return None if value is None else round(value, ndigits)


@rule("R01")
def first_transfer(ctx, f, p):
    return f.is_new_recipient, {"prior_transfers_to_recipient": len(f.prior_to_recipient)}


@rule("R02")
def unusual_amount(ctx, f, p):
    threshold = max(f.amount_p95, f.amount_median + p["mad_multiplier"] * f.amount_mad, p["min_amount_iqd"])
    return f.amount > threshold, {
        "amount": f.amount,
        "threshold": round(threshold),
        "p95": round(f.amount_p95),
        "median": round(f.amount_median),
        "mad": round(f.amount_mad),
        "n_prior_out": f.n_prior_out,
    }


@rule("R03")
def balance_drain(ctx, f, p):
    if f.balance_before <= 0:
        return False, {"balance_before": f.balance_before}
    remaining_share = (f.balance_before - f.amount) / f.balance_before
    fired = f.amount >= p["min_amount_iqd"] and remaining_share < p["remaining_share"]
    return fired, {"balance_before": f.balance_before, "remaining_share": _round(max(remaining_share, 0.0))}


@rule("R04")
def burst_or_rising(ctx, f, p):
    window_start = f.ts - timedelta(minutes=p["burst_window_min"])
    recent = sum(1 for t in f.prior_out_ts if t >= window_start)
    burst = recent + 1 >= p["burst_count"]

    rising = False
    prior = [t.amount for t in f.prior_to_recipient]
    if f.recipient_first_seen and len(prior) >= p["rising_min_prior"]:
        young = (f.ts - f.recipient_first_seen).days < p["rising_payee_max_age_days"]
        increasing = all(a < b for a, b in zip(prior, prior[1:], strict=False)) and f.amount > prior[-1]
        rising = young and increasing
    return burst or rising, {"outgoing_in_window": recent + 1, "rising_to_new_payee": rising}


@rule("R05")
def unusual_hour(ctx, f, p):
    hour = f.ts.hour
    if in_ramadan(f.ts) and hour in p["ramadan_night_hours"]:
        return False, {"hour": hour, "ramadan_night": True}
    total = sum(f.hour_counts)
    near = sum(f.hour_counts[(hour + k) % 24] for k in (-1, 0, 1))
    share = near / total if total else 0.0
    return share < p["min_hour_share"], {"hour": hour, "hour_share": _round(share, 3)}


@rule("R06")
def security_event(ctx, f, p):
    start = f.ts - timedelta(hours=p["window_hours"])
    events = [e for e in f.device_events if e.ts >= start and e.kind in p["kinds"]]
    return bool(events), {"events": sorted({e.kind for e in events})}


@rule("R07")
def active_call(ctx, f, p):
    return ctx.draft.call_active, {"call_active": ctx.draft.call_active}


@rule("R08")
def risky_recipient(ctx, f, p):
    r = ctx.recipient
    young = f.recipient_age_days is not None and f.recipient_age_days < p["max_age_days"]
    # Shops and billers are paid by many strangers by design; the mule signal is for personal accounts.
    many = not (r.is_business or r.is_biller) and r.unrelated_senders_7d >= p["unrelated_senders_7d"]
    return (r.reported or young or many), {
        "reported": r.reported,
        "account_age_days": _round(f.recipient_age_days, 1),
        "unrelated_senders_7d": r.unrelated_senders_7d,
        "business_account": r.is_business,
    }


@rule("R09")
def impersonation(ctx, f, p):
    if ctx.recipient.is_biller or ctx.recipient.is_business:
        return False, {"verified_account": True}
    texts = {"name": f"{ctx.draft.recipient_name} {ctx.recipient.display_name}", "note": ctx.draft.note}
    hits = {k: lexicons.find_terms(v, lexicons.IMPERSONATION_TERMS) for k, v in texts.items()}
    hits = {k: v for k, v in hits.items() if v}
    return bool(hits), {"matched_in": sorted(hits), "terms": sorted({t for v in hits.values() for t in v})}


@rule("R10")
def fee_words(ctx, f, p):
    cats = lexicons.find_fee_categories(ctx.draft.note)
    return bool(cats), {"categories": sorted(cats)}


@rule("R11")
def refund_after_credit(ctx, f, p):
    start = f.ts - timedelta(minutes=p["window_minutes"])
    credits = [c for c in f.stranger_credits if c.ts >= start]
    if not credits:
        return False, {}
    asks_refund = bool(lexicons.find_terms(ctx.draft.note, lexicons.REFUND_TERMS))
    same = [c for c in credits if c.counterparty == ctx.draft.recipient_wallet]
    fired = bool(same) or asks_refund
    credit = (same or credits)[-1]
    return fired, {
        "credit_amount": credit.amount,
        "minutes_since_credit": int((f.ts - credit.ts).total_seconds() // 60),
        "to_same_wallet": bool(same),
        "refund_words": asks_refund,
    }


@rule("R12")
def contact_lookalike(ctx, f, p):
    if f.saved_contact is not None:
        return False, {}
    name = ctx.draft.recipient_name or ctx.recipient.display_name
    best, best_sim = None, 0.0
    for c in ctx.contacts:
        if c.wallet == ctx.draft.recipient_wallet or c.saved_at > f.ts:
            continue
        sim = lexicons.name_similarity(name, c.name)
        if sim > best_sim:
            best, best_sim = c, sim
    fired = best is not None and best_sim >= p["min_similarity"]
    return fired, ({"contact_name": best.name, "similarity": _round(best_sim)} if fired else {})


@rule("R13")
def scam_cue_reason(ctx, f, p):
    reason = ctx.draft.reason_code
    fired = f.is_new_recipient and reason in p["cue_reasons"]
    return fired, {"reason": reason}


@rule("M01")
def recurring_payee(ctx, f, p):
    n = len(f.prior_to_recipient)
    span = (f.ts - f.recipient_first_seen).days if f.recipient_first_seen else 0
    return n >= p["min_prior"] and span >= p["min_span_days"], {"prior_transfers": n, "span_days": span}


@rule("M02")
def trusted_payee(ctx, f, p):
    if ctx.recipient.is_biller:
        return True, {"known_biller": True}
    c = f.saved_contact
    if c is None:
        return False, {"saved_contact": False}
    age = (f.ts - c.saved_at).days
    return age >= p["min_contact_age_days"], {"saved_contact": True, "contact_age_days": age}


@dataclass
class RuleResult:
    id: str
    kind: str
    points: int
    fired: bool
    en: str
    ar: str
    values: dict[str, Any] = field(default_factory=dict)
    disabled: str | None = None


@dataclass
class Assessment:
    score: int
    tier: str
    results: list[RuleResult]
    override: str | None
    rules_version: str
    fingerprint: str
    latency_ms: float

    @property
    def fired(self) -> list[RuleResult]:
        return [r for r in self.results if r.fired]

    @property
    def fired_ids(self) -> list[str]:
        return [r.id for r in self.fired]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fired_ids"] = self.fired_ids
        return d


def rules_fingerprint() -> str:
    h = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        h.update(name.encode())
        h.update((RULES_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


class Scorecard:
    def __init__(self, catalogue_path: Path | str = CATALOGUE_PATH):
        cat = yaml.safe_load(Path(catalogue_path).read_text(encoding="utf-8"))
        self.version: str = cat["version"]
        self.tiers = sorted(cat["tiers"], key=lambda t: t["min_score"])
        self.overrides = cat.get("overrides", [])
        self.min_history: int = cat["guardrails"]["min_history_for_personal"]
        self.rules: list[dict] = cat["rules"]
        self.reasons: list[dict] = cat.get("reasons", [])
        self.by_id = {r["id"]: r for r in self.rules}
        missing = [r["id"] for r in self.rules if r["id"] not in _RULES]
        if missing:
            raise ValueError(f"catalogue rules without an implementation: {missing}")
        self.fingerprint = rules_fingerprint()

    def tier_for(self, score: int) -> str:
        tier = self.tiers[0]["name"]
        for t in self.tiers:
            if score >= t["min_score"]:
                tier = t["name"]
        return tier

    def assess(self, ctx: DraftContext) -> Assessment:
        started = time.perf_counter()
        feats = compute_features(ctx)
        results = []
        for r in self.rules:
            res = RuleResult(id=r["id"], kind=r["kind"], points=r["points"], fired=False, en=r["en"], ar=r["ar"])
            if r.get("requires_history") and feats.n_prior_out < self.min_history:
                res.disabled = "cold_start"
                res.values = {"n_prior_out": feats.n_prior_out, "needs": self.min_history}
            else:
                res.fired, res.values = _RULES[r["id"]](ctx, feats, r.get("params") or {})
            results.append(res)

        score = max(0, sum(r.points for r in results if r.fired))
        tier = self.tier_for(score)
        fired = {r.id for r in results if r.fired}
        override = None
        rank = [t["name"] for t in self.tiers]
        for o in self.overrides:
            if fired & set(o.get("any_of", [])) and set(o.get("all_of", [])) <= fired:
                if rank.index(o["tier"]) > rank.index(tier):
                    tier, override = o["tier"], o["id"]
        return Assessment(
            score=score,
            tier=tier,
            results=results,
            override=override,
            rules_version=self.version,
            fingerprint=self.fingerprint,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def serialize_values(values: dict) -> dict:
    """Make rule values JSON-safe (datetimes to ISO strings)."""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in values.items()}
