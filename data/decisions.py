"""Turn simulated users into labelled pending-transfer decisions.

Scam decisions are injected from catalogue scripts (setup events + the scam draft); legitimate
decisions are sampled from real simulated history or constructed as hard negatives (honest
security events, honest calls, matched twins of scams). Each decision is independent: it sees the
user's base history before its timestamp plus its own setup, never another decision.
Labels go to a separate record the running system never reads.
"""

from __future__ import annotations

import bisect
import random
from datetime import datetime, timedelta

from data import pools
from data.scripts import DEV_FAMILIES, HELD_OUT_FAMILIES
from data.simulator import FAMILY_RELATIONS, SIBLING_FRIEND, SimUser, WalletBook, iqd
from rules.features import percentile

LEGIT_MIX = {  # per 20 users (the test split); dev and val scale by user count
    "matched_twin": 30, "cold_start": 40, "honest_security_event": 30, "honest_call": 25, "new_payee": 35,
    "unusual_amount": 20, "eid_gift": 10, "routine": 80, "bill": 30,
}
EID_NOTES = set(pools.ROUTINE_NOTES["eid"])
NEUTRAL_REASON = {"p2p": "family", "merchant": "purchase", "bill": "bill", "topup": "bill"}


class UserIndex:
    """Fast as-of lookups over one simulated user's history."""

    def __init__(self, su: SimUser):
        self.su = su
        self.hist = su.history
        self.ts = [t["ts"] for t in self.hist]
        self.signed_prefix = [0]
        for t in self.hist:
            self.signed_prefix.append(self.signed_prefix[-1] + (t["amount"] if t["direction"] == "in" else -t["amount"]))
        self.contacts_by_wallet = {c["wallet"]: c for c in su.contacts}

    def before(self, ts: datetime) -> int:
        return bisect.bisect_left(self.ts, ts)

    def balance_before(self, ts: datetime) -> int:
        return self.su.start_balance + self.signed_prefix[self.before(ts)]

    def prior_out(self, ts: datetime) -> list[dict]:
        return [t for t in self.hist[: self.before(ts)] if t["direction"] == "out"]


def _person_name(rng: random.Random) -> str:
    return f"{rng.choice(pools.MALE_FIRST + pools.FEMALE_FIRST)} {rng.choice(pools.LAST_NAMES)}"


def _iso(d: datetime) -> str:
    return d.isoformat()


def _recipient(wallet: str, name: str, created: datetime | None, senders: int = 0, reported: bool = False,
               is_biller: bool = False, is_business: bool = False) -> dict:
    return {"wallet": wallet, "display_name": name, "created_at": _iso(created) if created else None,
            "reported": reported, "is_biller": is_biller, "is_business": is_business, "unrelated_senders_7d": senders}


def _reason(rng: random.Random, cues: list[str], p_cue: float, neutral: list[str], p_neutral: float) -> str | None:
    r = rng.random()
    if cues and r < p_cue:
        return rng.choice(cues)
    if neutral and r < p_cue + p_neutral:
        return rng.choice(neutral)
    return None


def _mule(rng: random.Random, ts: datetime, p_young: float, p_many: float, p_reported: float = 0.1,
          young_days=(0, 6.9), old_days=(20, 300)) -> tuple[datetime, int, bool]:
    created = ts - timedelta(days=rng.uniform(*(young_days if rng.random() < p_young else old_days)))
    senders = rng.randint(8, 40) if rng.random() < p_many else rng.randint(0, 3)
    return created, senders, rng.random() < p_reported


class ScamInjector:
    def __init__(self, scripts: dict[str, dict], book: WalletBook):
        self.scripts = scripts
        self.book = book

    def _text(self, rng, fam, key, p) -> str:
        items = self.scripts[fam][key]
        return rng.choice(items) if items and rng.random() < p else ""

    def make(self, fam: str, ui: UserIndex, ts: datetime, rng: random.Random) -> dict | None:
        bal = ui.balance_before(ts)
        if bal < 20_000:
            return None
        setup = {"device_events": [], "extra_history": []}
        call = False
        reason = None
        if fam == "safe_account":
            wallet = self.book.new()
            name = self._text(rng, fam, "names", 0.65) or _person_name(rng)
            note = self._text(rng, fam, "notes", 0.45)
            call = rng.random() < 0.85
            if rng.random() < 0.25:
                setup["device_events"].append({"ts": _iso(ts - timedelta(hours=rng.uniform(0.5, 20))),
                                               "kind": rng.choice(["sim_change", "new_device"])})
            amount = bal * (rng.uniform(0.6, 0.98) if rng.random() < 0.75 else rng.uniform(0.2, 0.5))
            created, senders, reported = _mule(rng, ts, 0.7, 0.5)
            reason = _reason(rng, ["safe_account", "asked_for_code"], 0.25, ["family", "purchase"], 0.35)
        elif fam == "exchange_deposit":
            wallet = self.book.new()
            name = self._text(rng, fam, "names", 0.8) or _person_name(rng)
            note = self._text(rng, fam, "notes", 0.6)
            amount = min(bal * 0.95, rng.uniform(250_000, 2_500_000)) if bal > 120_000 else bal * rng.uniform(0.5, 0.9)
            call = rng.random() < 0.15
            created, senders, reported = _mule(rng, ts, 0.5, 0.5, old_days=(8, 200))
            reason = _reason(rng, ["exchange_deposit"], 0.2, ["purchase"], 0.4)
        elif fam == "prize_fee":
            wallet = self.book.new()
            name = self._text(rng, fam, "names", 0.7) or _person_name(rng)
            note = self._text(rng, fam, "notes", 0.55)
            amount = min(rng.choice([10_000, 15_000, 20_000, 25_000, 30_000, 50_000, 75_000, 100_000]), bal * 0.9)
            call = rng.random() < 0.5
            created, senders, reported = _mule(rng, ts, 0.5, 0.4)
            reason = _reason(rng, ["prize_fee", "unlock_money"], 0.35, ["family", "purchase"], 0.25)
        elif fam == "refund_after_credit":
            stranger = self.book.new()
            stranger_name = self._text(rng, fam, "names", 0.7)
            credit_ts = ts - timedelta(minutes=rng.uniform(10, 110))
            credit = iqd(rng.uniform(100_000, 1_000_000))
            setup["extra_history"].append({"ts": _iso(credit_ts), "direction": "in", "counterparty": stranger,
                                           "counterparty_name": stranger_name, "amount": credit, "note": "",
                                           "kind": "p2p"})
            bal += credit
            if rng.random() < 0.65:
                wallet, name = stranger, stranger_name
                created = ts - timedelta(days=rng.uniform(0, 30))
            else:
                wallet, name = self.book.new(), _person_name(rng)
                created = ts - timedelta(days=rng.uniform(0, 60))
            senders, reported = rng.randint(0, 5), rng.random() < 0.05
            amount = credit if rng.random() < 0.7 else min(credit * rng.uniform(1.0, 1.3), bal * 0.95)
            note = self._text(rng, fam, "notes", 0.5)
            call = rng.random() < 0.5
            reason = _reason(rng, [], 0, ["family"], 0.3)
        elif fam == "hacked_relative":
            people = [c for c in ui.su.contacts if c["relation"] in FAMILY_RELATIONS + SIBLING_FRIEND]
            if not people:
                return None
            c = rng.choice(people)
            if rng.random() < 0.6:
                wallet = self.book.new()
                if rng.random() < 0.7:
                    name = c["name"]
                else:
                    parts = c["name"].split()
                    name = " ".join(reversed(parts)) if len(parts) > 1 else f"{c['name']} الجديد"
                created, senders, reported = _mule(rng, ts, 0.5, 0.1, p_reported=0.05)
                reason = _reason(rng, [], 0, ["family"], 0.4)
            else:  # the relative's own account was taken over: a familiar, trusted payee
                wallet, name = c["wallet"], c["name"]
                acc = ui.su.accounts[wallet]
                created, senders, reported = acc["created_at"], acc["unrelated_senders_7d"], False
            note = self._text(rng, fam, "notes", 0.5)
            amount = min(rng.uniform(100_000, 750_000), bal * 0.9)
        elif fam == "marketplace_deposit":
            wallet = self.book.new()
            name = self._text(rng, fam, "names", 0.9) or _person_name(rng)
            note = self._text(rng, fam, "notes", 0.6)
            amount = min(rng.choice([15_000, 25_000, 40_000, 50_000, 75_000, 100_000, 150_000]), bal * 0.9)
            created = ts - timedelta(days=rng.uniform(3, 120))
            senders, reported = (rng.randint(5, 40) if rng.random() < 0.5 else rng.randint(0, 4)), rng.random() < 0.05
            call = rng.random() < 0.05
            reason = _reason(rng, [], 0, ["purchase"], 0.4)
        elif fam == "investment_platform":
            wallet = self.book.new()
            name = self._text(rng, fam, "names", 0.9) or _person_name(rng)
            first_seen = ts - timedelta(days=rng.uniform(2, 12))
            created = first_seen - timedelta(days=rng.uniform(3, 30))
            k = rng.randint(1, 3)
            amt = rng.uniform(50_000, 200_000)
            span = (ts - timedelta(hours=1) - first_seen).total_seconds()
            for i in range(k):
                step_ts = first_seen + timedelta(seconds=span * i / k)
                paid = iqd(min(amt, bal * 0.5))
                if paid < 10_000:
                    return None
                setup["extra_history"].append({"ts": _iso(step_ts), "direction": "out", "counterparty": wallet,
                                               "counterparty_name": name, "amount": paid,
                                               "note": self._text(rng, fam, "notes", 0.5), "kind": "p2p"})
                bal -= paid
                amt *= rng.uniform(1.3, 2.0)
            amount = min(amt * rng.uniform(1.0, 1.6), bal * 0.95)
            note = self._text(rng, fam, "notes", 0.5)
            senders, reported = (rng.randint(8, 60) if rng.random() < 0.6 else rng.randint(0, 4)), rng.random() < 0.1
            call = rng.random() < 0.1
        else:
            raise ValueError(fam)

        amount = iqd(amount)
        if amount < 5_000 or amount > bal:
            return None
        return {
            "ts": _iso(ts),
            "draft": {"recipient_wallet": wallet, "recipient_name": name, "amount": amount, "note": note,
                      "reason_code": reason, "call_active": call},
            "recipient": _recipient(wallet, name if wallet not in ui.su.accounts else ui.su.accounts[wallet]["display_name"],
                                    created, senders, reported),
            "setup": setup,
        }


def _legit_from_txn(ui: UserIndex, t: dict, rng: random.Random) -> dict:
    acc = ui.su.accounts.get(t["counterparty"], {})
    is_new = not any(o["counterparty"] == t["counterparty"] for o in ui.prior_out(t["ts"]))
    reason = None
    if is_new and rng.random() < 0.5:
        reason = NEUTRAL_REASON.get(t["kind"], "family")
    contact = ui.contacts_by_wallet.get(t["counterparty"])
    call = bool(contact and contact["relation"] in FAMILY_RELATIONS and rng.random() < 0.04)
    created = acc.get("created_at")
    return {
        "ts": _iso(t["ts"]),
        "draft": {"recipient_wallet": t["counterparty"], "recipient_name": t["counterparty_name"],
                  "amount": t["amount"], "note": t["note"], "reason_code": reason, "call_active": call},
        "recipient": _recipient(t["counterparty"], acc.get("display_name", t["counterparty_name"]), created,
                                acc.get("unrelated_senders_7d", 0), acc.get("reported", False), acc.get("is_biller", False),
                                acc.get("is_business", False)),
        "setup": {"device_events": [], "extra_history": []},
    }


def legit_pools(ui: UserIndex) -> dict[str, list[dict]]:
    """Categorise every outgoing transaction of one user (after a warm-up for established users)."""
    out: dict[str, list[dict]] = {k: [] for k in LEGIT_MIX}
    warm = ui.su.sim_start + timedelta(days=30 if ui.su.user["segment"] != "first_time" else 0)
    prior_amounts: list[float] = []
    seen: set[str] = set()
    for t in ui.hist:
        if t["direction"] != "out":
            continue
        n_prior = len(prior_amounts)
        is_new = t["counterparty"] not in seen
        if t["ts"] >= warm:
            if n_prior < 10:
                cat = "cold_start"
            elif t["note"] in EID_NOTES:
                cat = "eid_gift"
            elif t["kind"] in ("bill", "topup"):
                cat = "bill"
            elif is_new:
                cat = "new_payee"
            elif t["amount"] > percentile(prior_amounts, 95):
                cat = "unusual_amount"
            else:
                cat = "routine"
            out[cat].append(t)
        prior_amounts.append(float(t["amount"]))
        seen.add(t["counterparty"])
    return out


def build_decisions(split: str, users: list[SimUser], scripts: dict, book: WalletBook, seed: int,
                    held_out: bool) -> tuple[list[dict], list[dict]]:
    rng = random.Random(f"{seed}-{split}-decisions")
    scale = len(users) / 20
    index = {su.user["id"]: UserIndex(su) for su in users}
    injector = ScamInjector(scripts, book)

    families = DEV_FAMILIES + (HELD_OUT_FAMILIES if held_out else [])
    if held_out:  # test: 70 scams from known families + 30 from two families never used in tuning
        per_family = {**{f: 14 for f in DEV_FAMILIES}, **{f: 15 for f in HELD_OUT_FAMILIES}}
    else:  # dev/val: one scam per user per family, keeping the 1:3 scam:legit ratio of the test set
        per_family = {f: len(users) for f in DEV_FAMILIES}

    records: list[tuple[dict, dict]] = []
    scams: list[tuple[dict, dict]] = []
    for fam in families:
        made, attempts = 0, 0
        while made < per_family[fam] and attempts < per_family[fam] * 30:
            attempts += 1
            su = rng.choice(users)
            ui = index[su.user["id"]]
            lo = max(su.sim_start + timedelta(days=3), su.sim_end - timedelta(days=60))
            if lo >= su.sim_end - timedelta(hours=2):
                continue
            ts = lo + timedelta(seconds=rng.uniform(0, (su.sim_end - timedelta(hours=2) - lo).total_seconds()))
            ts = ts.replace(hour=rng.randint(9, 22), microsecond=0)
            if ts >= su.sim_end:
                continue
            dec = injector.make(fam, ui, ts, rng)
            if dec is None:
                continue
            dec["user_id"] = su.user["id"]
            label = {"is_scam": True, "family": fam, "category": "scam", "held_out_family": fam in HELD_OUT_FAMILIES,
                     "pair_id": None}
            scams.append((dec, label))
            made += 1
    records += scams

    # Legitimate decisions
    all_pools: dict[str, list[tuple[UserIndex, dict]]] = {k: [] for k in LEGIT_MIX}
    for ui in index.values():
        for cat, txns in legit_pools(ui).items():
            all_pools[cat] += [(ui, t) for t in txns]
    targets = {k: round(v * scale) for k, v in LEGIT_MIX.items()}
    used: set[tuple[str, str]] = set()

    def take(cat: str, n: int, source: list[tuple[UserIndex, dict]]) -> list[tuple[UserIndex, dict]]:
        cand = [x for x in source if (x[0].su.user["id"], _iso(x[1]["ts"])) not in used]
        rng.shuffle(cand)
        picked = cand[:n]
        for ui, t in picked:
            used.add((ui.su.user["id"], _iso(t["ts"])))
        return picked

    shortfall = 0
    for cat in ("cold_start", "eid_gift", "bill", "new_payee", "unusual_amount"):
        picked = take(cat, targets[cat], all_pools[cat])
        shortfall += targets[cat] - len(picked)
        for ui, t in picked:
            dec = {**_legit_from_txn(ui, t, rng), "user_id": ui.su.user["id"]}
            records.append((dec, {"is_scam": False, "family": None, "category": cat, "held_out_family": False,
                                  "pair_id": None}))

    # Honest security events: a normal transfer a few hours after a new phone, PIN reset or SIM swap.
    for ui, t in take("honest_security_event", targets["honest_security_event"],
                      all_pools["routine"] + all_pools["new_payee"]):
        dec = {**_legit_from_txn(ui, t, rng), "user_id": ui.su.user["id"]}
        dec["setup"]["device_events"].append({"ts": _iso(t["ts"] - timedelta(hours=rng.uniform(1, 20))),
                                              "kind": rng.choice(["new_device", "pin_reset", "sim_change"])})
        records.append((dec, {"is_scam": False, "family": None, "category": "honest_security_event",
                              "held_out_family": False, "pair_id": None}))

    # Honest calls: sending money to family while on the phone with them.
    family_txns = [(ui, t) for ui, t in all_pools["routine"]
                   if (c := ui.contacts_by_wallet.get(t["counterparty"])) and c["relation"] in FAMILY_RELATIONS + SIBLING_FRIEND]
    for ui, t in take("honest_call", targets["honest_call"], family_txns):
        dec = {**_legit_from_txn(ui, t, rng), "user_id": ui.su.user["id"]}
        dec["draft"]["call_active"] = True
        records.append((dec, {"is_scam": False, "family": None, "category": "honest_call", "held_out_family": False,
                              "pair_id": None}))

    # Matched twins: same user, time, amount, call flag and device events as a scam, honest recipient.
    twins = rng.sample(scams, min(targets["matched_twin"], len(scams)))
    for k, (sdec, slabel) in enumerate(twins):
        pair = f"{split}-pair-{k:03d}"
        slabel["pair_id"] = pair
        ts = datetime.fromisoformat(sdec["ts"])
        wallet = book.new()
        name = _person_name(rng)
        created = ts - timedelta(days=rng.uniform(60, 1500))
        dec = {
            "ts": sdec["ts"], "user_id": sdec["user_id"],
            "draft": {**sdec["draft"], "recipient_wallet": wallet, "recipient_name": name,
                      "note": rng.choice(["", "", "حساب", "دين"]), "reason_code": rng.choice([None, "family", "purchase"])},
            "recipient": _recipient(wallet, name, created, rng.randint(0, 3)),
            "setup": {"device_events": list(sdec["setup"]["device_events"]), "extra_history": []},
        }
        bal = index[sdec["user_id"]].balance_before(ts)
        dec["draft"]["amount"] = min(dec["draft"]["amount"], iqd(bal * 0.98)) if bal > 5_000 else dec["draft"]["amount"]
        records.append((dec, {"is_scam": False, "family": slabel["family"], "category": "matched_twin",
                              "held_out_family": False, "pair_id": pair}))

    # Any category that ran short is filled with routine transfers so the total stays fixed.
    for ui, t in take("routine", targets["routine"] + shortfall, all_pools["routine"]):
        dec = {**_legit_from_txn(ui, t, rng), "user_id": ui.su.user["id"]}
        records.append((dec, {"is_scam": False, "family": None, "category": "routine", "held_out_family": False,
                              "pair_id": None}))

    rng.shuffle(records)
    decisions, labels = [], []
    for i, (dec, label) in enumerate(records, 1):
        did = f"{split}-{i:04d}"
        decisions.append({"decision_id": did, "split": split, **dec})
        labels.append({"decision_id": did, "user_id": dec["user_id"], **label})
    return decisions, labels
