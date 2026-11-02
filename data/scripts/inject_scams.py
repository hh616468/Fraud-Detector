"""Plant scams into simulated wallet histories and write the labels where the detector cannot read them.

Each scam type is a script: a mule account, maybe a security event or a bait credit, then one or more
transfers out. Every signal (call flag, account age, note, reason tap) is drawn from a per-type chance
based on how the scam works, never switched on to match a rule, so some scams slip past the rules.

Also makes matched pairs: an honest transfer in the same user that looks the same as a scam transfer
(same share of balance, account age, call flag, note and tap) but has a different intent.

Outputs:
    data/<split>/wallet/        what the detector reads: users, contacts, recipients, transactions, device events
    data/labels/<split>_labels.csv   evaluation only

Usage:
    python data/scripts/inject_scams.py --split devval --cases 80 --pairs 15
    python data/scripts/inject_scams.py --split test --target-scams 100 --pairs 20 --seed 7731
"""

import argparse
import csv
import json
import math
import random
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from generate_scam_text import HELD_OUT, SCAM_TYPES
from simulate_history import (END, FIRST_NAMES, KURDISH_CITIES, KURDISH_SURNAMES, START, SURNAMES, round_iqd,
                              user_time, write_csv)

DATA = Path(__file__).resolve().parents[1]

# Chance of each signal per scam type. Our assumptions from how each scam is carried out (CBI warnings,
# press reports), not measured rates; the README lists them. cue = picks a scam cue when answering the tap.
P = {
    "impersonation":   dict(call=.70, security=.25, young=.5, fake_name=.5, note=.3, cue=.35),
    "fake_exchange":   dict(call=.05, security=.05, young=.4, fake_name=.6, note=.3, cue=.10),
    "prize_fee":       dict(call=.30, security=.15, young=.5, fake_name=.4, note=.6, cue=.45),
    "refund_scam":     dict(call=.40, security=.05, young=.4, fake_name=.1, note=.5, cue=0),
    "hacked_relative": dict(call=.10, security=.05, young=.3, fake_name=.1, note=.4, cue=0),
    "investment":      dict(call=.10, security=.05, young=.3, fake_name=.2, note=.4, cue=.10),
    "fake_seller":     dict(call=.10, security=.03, young=.4, fake_name=.1, note=.6, cue=0),
}
MULE = dict(reuse=.3, reported=.15, reported_if_reused=.3, many_senders=.5)
TOPUP_FOR_SCAM = .6  # victim short of the asked amount goes to an agent to cash in (else sends what they have)
TAP_SKIP = .4
CUES = {"impersonation": ["protect_my_money", "someone_asked_for_code"], "prize_fee": ["prize_or_fee"],
        "fake_exchange": ["protect_my_money"], "investment": ["prize_or_fee"]}
BENIGN_TAP = {"impersonation": "other", "fake_exchange": "purchase", "prize_fee": "other",
              "refund_scam": "pay_back_debt", "hacked_relative": "family_help", "investment": "other",
              "fake_seller": "purchase"}
FAMILY_OR_FRIEND = {"parent", "child", "sibling", "spouse", "relative", "friend"}
TXN_FIELDS = ["txn_id", "user_id", "ts", "direction", "type", "counterparty_id", "amount_iqd", "balance_after",
              "note", "device_id", "call_active", "reason_tap"]


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Injector:
    def __init__(self, split, seed):
        history = DATA / split / "history"
        self.rng = random.Random(f"{seed}:inject:{split}")
        self.personas = {p["user_id"]: p for p in map(json.loads, open(DATA / split / "personas.jsonl", encoding="utf-8"))}
        self.users = {u["user_id"]: u for u in read_csv(history / "users.csv")}
        self.tag = next(iter(self.users))[0]
        self.recipients = read_csv(history / "recipients.csv")
        self.rec = {r["recipient_id"]: r for r in self.recipients}
        self.phones = {r["phone"] for r in self.recipients} | {u["phone"] for u in self.users.values()}
        self.contacts = defaultdict(list)
        for c in read_csv(history / "contacts.csv"):
            self.contacts[c["user_id"]].append(c)
        self.ledger = defaultdict(list)
        for t in read_csv(history / "transactions.csv"):
            t.pop("txn_id")
            t.update(ts=datetime.fromisoformat(t["ts"]), amount_iqd=int(t["amount_iqd"]),
                     call_active=t["call_active"] == "True", by_user=t["device_id"] != "")
            self.ledger[t["user_id"]].append(t)
        self.devices = defaultdict(list)
        for d in read_csv(history / "device_events.csv"):
            d["ts"] = datetime.fromisoformat(d["ts"])
            self.devices[d["user_id"]].append(d)
        text = json.load(open(DATA / split / "scam_text.json", encoding="utf-8"))
        self.text = text["types"]
        self.mules = defaultdict(list)       # scam type -> mule recipient ids, for reuse across victims
        self.senders = {}                    # recipient id -> inbound_senders_30d, set for mules and twins
        self.case_n = 0
        self.pair_n = 0

    # ---- accounts -------------------------------------------------------------------------------
    def add_recipient(self, name, kind, city, created, reported=False):
        rid = f"R{self.tag}{len(self.recipients) + 1:05d}"
        while True:
            phone = self.rng.choice(["078", "077", "075"]) + f"{self.rng.randint(0, 99_999_999):08d}"
            if phone not in self.phones:
                break
        self.phones.add(phone)
        r = {"recipient_id": rid, "display_name": name, "phone": phone, "kind": kind, "city": city,
             "created_at": created.isoformat(), "is_biller": False, "reported": reported}
        self.recipients.append(r)
        self.rec[rid] = r
        return rid

    def ordinary_name(self, city):
        surnames = KURDISH_SURNAMES if city in KURDISH_CITIES else SURNAMES
        return f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(surnames)}"

    def mule(self, stype, ts, city, name=None):
        """A receiving account for this scam: reused from earlier victims or opened for this one."""
        rng, p = self.rng, P[stype]
        usable = [m for m in self.mules[stype] if date.fromisoformat(self.rec[m]["created_at"]) < ts.date()]
        if name is None and usable and rng.random() < MULE["reuse"]:
            rid = rng.choice(usable)
            if rng.random() < MULE["reported_if_reused"]:
                self.rec[rid]["reported"] = True
            return rid
        fake = name is None and rng.random() < p["fake_name"]
        if name is None:
            name = rng.choice(self.text[stype]["account_names"]) if fake else self.ordinary_name(city)
        age = rng.randint(0, 6) if rng.random() < p["young"] else rng.randint(7, 700)
        rid = self.add_recipient(name, "merchant" if fake and rng.random() < .5 else "person", city,
                                 ts.date() - timedelta(days=age), rng.random() < MULE["reported"])
        self.senders[rid] = rng.randint(15, 120) if rng.random() < MULE["many_senders"] else rng.randint(1, 6)
        self.mules[stype].append(rid)
        return rid

    # ---- ledger ---------------------------------------------------------------------------------
    def balance_at(self, uid, ts):
        bal = int(self.users[uid]["opening_balance_iqd"])
        for t in self.ledger[uid]:
            if t["ts"] >= ts:
                break
            bal += t["amount_iqd"] if t["direction"] == "in" else -t["amount_iqd"]
        return bal

    def agent(self, uid):
        ids = [t["counterparty_id"] for t in self.ledger[uid] if t["type"] == "cash_in"]
        if ids:
            return max(set(ids), key=ids.count)
        city = self.users[uid]["city"]
        return self.add_recipient(f"وكيل زين كاش - {city}", "agent", city, START - timedelta(days=900))

    def row(self, uid, ts, direction, kind, cp, amount, note="", call=False, tap="", by_user=None, label=None):
        r = {"user_id": uid, "ts": ts, "direction": direction, "type": kind, "counterparty_id": cp,
             "amount_iqd": amount, "note": note, "device_id": "", "call_active": call, "reason_tap": tap,
             "by_user": direction == "out" if by_user is None else by_user}
        if label:
            r["label"] = label
        self.ledger[uid].append(r)
        self.ledger[uid].sort(key=lambda t: t["ts"])
        return r

    def rewalk(self, uid):
        """Recompute balances in time order; cash in at an agent before any transfer the balance can't cover."""
        bal = int(self.users[uid]["opening_balance_iqd"])
        rows = []
        for t in sorted(self.ledger[uid], key=lambda t: t["ts"]):
            if t["direction"] == "out" and t["amount_iqd"] > bal:
                need = math.ceil((t["amount_iqd"] - bal + self.rng.uniform(0, 50_000)) / 25_000) * 25_000
                bal += need
                rows.append({"user_id": uid, "ts": t["ts"] - timedelta(minutes=self.rng.randint(5, 60)),
                             "direction": "in", "type": "cash_in", "counterparty_id": self.agent(uid),
                             "amount_iqd": need, "balance_after": bal, "note": "", "device_id": "",
                             "call_active": False, "reason_tap": "", "by_user": True})
            bal += t["amount_iqd"] if t["direction"] == "in" else -t["amount_iqd"]
            t["balance_after"] = bal
            rows.append(t)
        self.ledger[uid] = rows

    def assign_devices(self, uid):
        events = sorted(self.devices[uid], key=lambda d: d["ts"])
        n = 1
        for d in events:
            n += d["event"] == "new_device"
            d["device_id"] = f"{uid}-d{n}"
        self.devices[uid] = events
        i = 0
        for t in self.ledger[uid]:
            while i < len(events) and events[i]["ts"] <= t["ts"]:
                i += 1
            t["device_id"] = (events[i - 1]["device_id"] if i else f"{uid}-d1") if t["by_user"] else ""

    def security_event(self, uid, ts, kind=None, hours=None):
        kind = kind or self.rng.choice(["sim_change", "pin_reset", "new_device"])
        hours = hours if hours is not None else self.rng.uniform(0.5, 20)
        self.devices[uid].append({"user_id": uid, "ts": ts - timedelta(hours=hours), "event": kind,
                                  "device_id": ""})
        return kind, hours

    # ---- scam transfers -------------------------------------------------------------------------
    def tap(self, uid, cp, ts, stype, rng):
        """Reason tap, asked only for a payee this user has never paid and has not saved."""
        if any(c["recipient_id"] == cp for c in self.contacts[uid]):
            return ""
        if any(t["direction"] == "out" and t["counterparty_id"] == cp and t["ts"] < ts for t in self.ledger[uid]):
            return ""
        if rng.random() < TAP_SKIP:
            return ""
        return rng.choice(CUES[stype]) if stype in CUES and rng.random() < P[stype]["cue"] else BENIGN_TAP[stype]

    def send(self, uid, ts, stype, cp, target, label, cash_in_ok=True):
        """One scam transfer of `target` IQD; a short victim cashes in first or sends what they have."""
        rng = self.rng
        bal = self.balance_at(uid, ts)
        if target > bal:
            if cash_in_ok and (rng.random() < TOPUP_FOR_SCAM or bal < 5_000):
                need = math.ceil((target - bal) / 25_000) * 25_000
                self.row(uid, ts - timedelta(minutes=rng.randint(20, 120)), "in", "cash_in", self.agent(uid), need,
                         by_user=True, label=dict(label, step="victim_cash_in"))
            else:
                target = round_iqd(bal * rng.uniform(0.8, 1.0))
        note = rng.choice(self.text[stype]["notes"]) if rng.random() < P[stype]["note"] else ""
        r = self.row(uid, ts, "out", "p2p", cp, target, note, rng.random() < P[stype]["call"],
                     self.tap(uid, cp, ts, stype, rng), label=dict(label, step="transfer"))
        r["share_of_balance"] = target / max(self.balance_at(uid, ts), 1)
        return r

    def run_case(self, stype, uid):
        rng, persona = self.rng, self.personas[uid]
        city = self.users[uid]["city"]
        self.case_n += 1
        label = {"case_id": f"C{self.tag}{self.case_n:04d}", "scam_type": stype, "is_scam": 1, "pair_id": ""}
        first = max(START, date.fromisoformat(self.users[uid]["wallet_created_at"])) + timedelta(days=14)
        day = first + timedelta(days=rng.randint(0, max(0, (END - first).days - 3)))
        t0 = user_time(rng, day, persona)
        security = self.security_event(uid, t0) if rng.random() < P[stype]["security"] else None
        out = []

        def later(ts, **kw):
            nxt = ts + timedelta(**kw)
            return nxt if nxt.date() <= END else None

        if stype == "impersonation":
            cp = self.mule(stype, t0, city)
            bal = self.balance_at(uid, t0)
            target = round_iqd(bal * rng.uniform(.6, .95)) if bal >= 50_000 else round_iqd(rng.uniform(1e5, 5e5))
            out.append(self.send(uid, t0, stype, cp, target, label))
            if rng.random() < .3 and (t1 := later(t0, minutes=rng.randint(10, 60))):
                rest = self.balance_at(uid, t1)
                if rest >= 5_000:
                    out.append(self.send(uid, t1, stype, cp, round_iqd(rest * rng.uniform(.7, 1)), label, False))
        elif stype in ("fake_exchange", "prize_fee", "investment"):
            n, lo_hi, grow, gap = {
                "fake_exchange": (rng.randint(1, 3), (250_000, 1_000_000), (1.3, 2.0), {"days": (1, 10)}),
                "prize_fee": (rng.randint(1, 3), (25_000, 150_000), (1.2, 2.0), {"hours": (1, 48)}),
                "investment": (rng.randint(2, 4), (50_000, 250_000), (1.5, 2.5), {"days": (2, 10)}),
            }[stype]
            cp, ts, amount = self.mule(stype, t0, city), t0, rng.uniform(*lo_hi)
            for i in range(n):
                if i:
                    unit, (a, b) = next(iter(gap.items()))
                    if not (ts := later(ts, **{unit: rng.uniform(a, b)})):
                        break
                    amount *= rng.uniform(*grow)
                out.append(self.send(uid, ts, stype, cp, round_iqd(amount), label))
        elif stype == "refund_scam":
            bait = self.mule(stype, t0, city)
            credit = round_iqd(rng.uniform(50_000, 500_000))
            self.row(uid, t0, "in", "p2p", bait, credit, label=dict(label, step="bait_credit"))
            hours = rng.uniform(.2, 2) if rng.random() < .55 else rng.uniform(2, 8)
            ts = t0 + timedelta(hours=hours)
            cp = bait if rng.random() < .5 else self.mule(stype, ts, city)
            amount = credit if rng.random() < .7 else round_iqd(credit * rng.uniform(1.5, 3))
            r = self.send(uid, ts, stype, cp, amount, label)
            r["type"] = "refund"
            out.append(r)
        elif stype == "hacked_relative":
            ids = [c["recipient_id"] for c, pc in zip(self.contacts[uid], persona["contacts"])
                   if pc["relation"] in FAMILY_OR_FRIEND]
            real = self.rec[rng.choice(ids)]["display_name"] if ids else self.ordinary_name(city)
            roll = rng.random()
            name = real if roll < .5 else f"{real.split()[0]} {self.ordinary_name(city).split()[-1]}" if roll < .8 \
                else None
            cp = self.mule(stype, t0, city, name)
            out.append(self.send(uid, t0, stype, cp, round_iqd(rng.uniform(100_000, 750_000)), label))
            if rng.random() < .2 and (t1 := later(t0, hours=rng.uniform(1, 24))):
                out.append(self.send(uid, t1, stype, cp, round_iqd(out[0]["amount_iqd"] * rng.uniform(.5, 1)), label))
        elif stype == "fake_seller":
            cp = self.mule(stype, t0, city)
            out.append(self.send(uid, t0, stype, cp, round_iqd(rng.uniform(100_000, 1_000_000)), label))
            if rng.random() < .15 and (t1 := later(t0, hours=rng.uniform(1, 24))):
                out.append(self.send(uid, t1, stype, cp, round_iqd(out[0]["amount_iqd"] * rng.uniform(.1, .3)), label))
        self.rewalk(uid)
        return out, security

    def twin(self, uid, scam, security):
        """An honest transfer made to look like `scam`: same share of balance, account age, call, note, tap."""
        rng = self.rng
        self.pair_n += 1
        pair_id = f"P{self.tag}{self.pair_n:03d}"
        scam["label"]["pair_id"] = pair_id
        mule = self.rec[scam["counterparty_id"]]
        first = max(START, date.fromisoformat(self.users[uid]["wallet_created_at"])) + timedelta(days=14)
        for _ in range(20):
            day = first + timedelta(days=rng.randint(0, max(0, (END - first).days)))
            if abs((day - scam["ts"].date()).days) >= 7:
                break
        ts = user_time(rng, day, self.personas[uid])
        age = (scam["ts"].date() - date.fromisoformat(mule["created_at"])).days
        name = mule["display_name"] if any(  # a relative's real new number: keep the matching name
            self.rec[c["recipient_id"]]["display_name"] == mule["display_name"] for c in self.contacts[uid]) \
            else self.ordinary_name(self.users[uid]["city"])
        cp = self.add_recipient(name, mule["kind"], self.users[uid]["city"], ts.date() - timedelta(days=age),
                                mule["reported"])
        self.senders[cp] = self.senders.get(mule["recipient_id"], rng.randint(1, 6))
        if security:
            self.security_event(uid, ts, *security)
        bal = self.balance_at(uid, ts)
        amount = round_iqd(max(bal * scam["share_of_balance"], 5_000))
        self.row(uid, ts, "out", "p2p" if scam["type"] != "refund" else "refund", cp, amount, scam["note"],
                 scam["call_active"], scam["reason_tap"],
                 label={"case_id": "", "scam_type": scam["label"]["scam_type"], "is_scam": 0, "pair_id": pair_id,
                        "step": "matched_twin"})
        self.rewalk(uid)

    # ---- output ---------------------------------------------------------------------------------
    def finish(self, split):
        rng = self.rng
        for r in self.recipients:  # how many different senders paid this account in the last 30 days
            if r["recipient_id"] in self.senders:
                n = self.senders[r["recipient_id"]]
            elif r["kind"] in ("biller", "agent", "employer"):
                n = rng.randint(300, 5000)
            elif r["kind"] == "merchant":
                n = max(2, min(800, int(math.exp(rng.gauss(math.log(25), 1)))))
            else:
                n = rng.randint(1, 6) if rng.random() < .9 else rng.randint(7, 20)
            r["inbound_senders_30d"] = n
        for uid in self.ledger:
            self.assign_devices(uid)
        rows = sorted((t for rows in self.ledger.values() for t in rows), key=lambda t: (t["ts"], t["user_id"]))
        labels = []
        for i, t in enumerate(rows, 1):
            t["txn_id"] = f"X{self.tag}{i:06d}"
            if "label" in t:
                labels.append({"txn_id": t["txn_id"], "user_id": t["user_id"], "direction": t["direction"], **t["label"]})
        out_rows = [{k: (v.isoformat() if k == "ts" else v) for k, v in t.items() if k in TXN_FIELDS} for t in rows]
        devices = sorted((dict(d, ts=d["ts"].isoformat()) for ds in self.devices.values() for d in ds),
                         key=lambda d: (d["user_id"], d["ts"]))

        wallet, history = DATA / split / "wallet", DATA / split / "history"
        wallet.mkdir(exist_ok=True)
        for name in ("users.csv", "contacts.csv"):  # unchanged by injection; copied so wallet/ is complete
            shutil.copyfile(history / name, wallet / name)
        write_csv(wallet / "transactions.csv", out_rows, TXN_FIELDS)
        write_csv(wallet / "recipients.csv", self.recipients,
                  ["recipient_id", "display_name", "phone", "kind", "city", "created_at", "is_biller", "reported",
                   "inbound_senders_30d"])
        write_csv(wallet / "device_events.csv", devices, ["user_id", "ts", "event", "device_id"])
        (DATA / "labels").mkdir(exist_ok=True)
        write_csv(DATA / "labels" / f"{split}_labels.csv", labels,
                  ["txn_id", "user_id", "direction", "case_id", "scam_type", "step", "is_scam", "pair_id"])
        return labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["devval", "test"])
    ap.add_argument("--cases", type=int, default=80, help="scam cases to plant (ignored with --target-scams)")
    ap.add_argument("--target-scams", type=int, default=0, help="plant cases until this many scam transfers out")
    ap.add_argument("--pairs", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261030)
    args = ap.parse_args()

    inj = Injector(args.split, args.seed)
    rng = inj.rng
    types = [t for t in SCAM_TYPES if args.split == "test" or t not in HELD_OUT]
    # Held-out types get half the cases of the others, so they don't dominate the test set.
    cycle = [t for t in types if t not in HELD_OUT] * 2 + [t for t in types if t in HELD_OUT]
    users = sorted(inj.users)
    load = defaultdict(int)
    pair_every = 3  # offer every third eligible case for a twin, so pairs spread across types
    n_scam_out = n_cases = eligible = 0
    while (n_scam_out < args.target_scams) if args.target_scams else (n_cases < args.cases):
        stype = cycle[n_cases % len(cycle)]
        uid = min(rng.sample(users, 3), key=lambda u: load[u])  # spread cases across users
        load[uid] += 1
        out, security = inj.run_case(stype, uid)
        n_cases += 1
        n_scam_out += len(out)
        if out and inj.pair_n < args.pairs:
            eligible += 1
            if eligible % pair_every == 0 or args.pairs - inj.pair_n >= (args.cases or args.target_scams) - n_cases:
                inj.twin(uid, out[0], security)

    labels = inj.finish(args.split)
    scam_out = [l for l in labels if l["is_scam"] == 1 and l["direction"] == "out"]
    summary = {
        "split": args.split, "seed": args.seed, "cases": n_cases, "scam_transfers_out": len(scam_out),
        "matched_twins": sum(l["step"] == "matched_twin" for l in labels),
        "by_type": {t: sum(l["scam_type"] == t for l in scam_out) for t in types},
        "assumptions": {"per_type": P, "mule": MULE, "topup_for_scam": TOPUP_FOR_SCAM, "tap_skip": TAP_SKIP},
    }
    (DATA / "labels" / f"{args.split}_labels.manifest.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "assumptions"}, indent=1))


if __name__ == "__main__":
    main()
