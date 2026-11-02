"""Split users and draw the decision sets the detector is scored on.

- eval/splits.json: 40 dev/val users split by user into 30 dev and 10 val (stratified by segment); 20 test users.
- eval/decisions_<set>.csv: the outgoing transfers to score, ids only, in time order. The detector computes its
  features from data/<split>/wallet/ using only history before each transfer.
- labels/decisions_<set>_labels.csv: the answer key, kept apart.

Each set holds every scam transfer of its users, every matched twin, and legitimate transfers to fill the
rest. Half of those legitimate ones are hard cases (they show at least one risk signal: new payee, drain,
young account, call, security event, fee word, unusually large amount), so the false-alarm rate is
measured on transfers that look suspicious, not just on easy ones.

Usage:
    python data/scripts/build_eval_set.py
"""

import argparse
import csv
import json
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parents[1]
LABELS, EVAL = DATA / "labels", DATA / "eval"
FEE_WORDS = ["رسوم", "جائزة", "تفعيل", "منحة", "عربون"]


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def split_users(rng, n_val):
    """Validation users are drawn per segment so both sets keep the same mix."""
    users = read_csv(DATA / "devval" / "wallet" / "users.csv")
    by_seg = defaultdict(list)
    for u in sorted(users, key=lambda u: u["user_id"]):
        by_seg[u["segment"]].append(u["user_id"])
    val = []
    for seg, ids in sorted(by_seg.items()):
        val += rng.sample(ids, round(n_val * len(ids) / len(users)))
    dev = sorted(u["user_id"] for u in users if u["user_id"] not in val)
    test = sorted(u["user_id"] for u in read_csv(DATA / "test" / "wallet" / "users.csv"))
    return {"dev": dev, "val": sorted(val), "test": test}


def hard_signals(split):
    """Which legitimate outgoing transfers show at least one risk signal (no rule weights involved)."""
    wallet = DATA / split / "wallet"
    tx = read_csv(wallet / "transactions.csv")
    rec = {r["recipient_id"]: r for r in read_csv(wallet / "recipients.csv")}
    saved = defaultdict(set)
    for c in read_csv(wallet / "contacts.csv"):
        saved[c["user_id"]].add(c["recipient_id"])
    sec = defaultdict(list)
    for d in read_csv(wallet / "device_events.csv"):
        if d["event"] != "account_created":
            sec[d["user_id"]].append(datetime.fromisoformat(d["ts"]))

    paid, amounts, hard = defaultdict(set), defaultdict(list), {}
    for t in tx:  # transactions are in time order
        if t["direction"] != "out":
            continue
        uid, cp, amt = t["user_id"], t["counterparty_id"], int(t["amount_iqd"])
        ts, after = datetime.fromisoformat(t["ts"]), int(t["balance_after"])
        r = rec[cp]
        past = amounts[uid]
        reasons = [name for name, hit in [
            ("new_payee", cp not in paid[uid] and cp not in saved[uid]),
            ("drain", after < 0.2 * (after + amt)),
            ("young_account", (ts.date() - date.fromisoformat(r["created_at"])).days < 7),
            ("call", t["call_active"] == "True"),
            ("security_24h", any(timedelta(0) <= ts - s <= timedelta(hours=24) for s in sec[uid])),
            ("fee_word", any(w in t["note"] for w in FEE_WORDS)),
            ("large_amount", len(past) >= 10 and amt > 3 * statistics.median(past)),
        ] if hit]
        hard[t["txn_id"]] = reasons
        paid[uid].add(cp)
        past.append(amt)
    return tx, hard


def build(name, split, users, size, rng):
    tx, hard = hard_signals(split)
    labels = {l["txn_id"]: l for l in read_csv(LABELS / f"{split}_labels.csv")}
    out = [t for t in tx if t["direction"] == "out" and t["user_id"] in users]
    scams = [t for t in out if labels.get(t["txn_id"], {}).get("is_scam") == "1"]
    twins = [t for t in out if labels.get(t["txn_id"], {}).get("step") == "matched_twin"]
    legit = [t for t in out if t["txn_id"] not in labels]
    n_legit = max(0, size - len(scams) - len(twins)) if size else 3 * len(scams) - len(twins)
    hard_pool = [t for t in legit if hard[t["txn_id"]]]
    easy_pool = [t for t in legit if not hard[t["txn_id"]]]
    n_hard = min(len(hard_pool), n_legit // 2)
    picked = rng.sample(hard_pool, n_hard) + rng.sample(easy_pool, n_legit - n_hard)

    stratum = {t["txn_id"]: "scam" for t in scams} | {t["txn_id"]: "matched_twin" for t in twins} | \
              {t["txn_id"]: "hard_legit" if hard[t["txn_id"]] else "random_legit" for t in picked}
    chosen = sorted(scams + twins + picked, key=lambda t: t["txn_id"])
    write_csv(EVAL / f"decisions_{name}.csv", [{"txn_id": t["txn_id"], "user_id": t["user_id"], "ts": t["ts"]}
                                              for t in chosen], ["txn_id", "user_id", "ts"])
    write_csv(LABELS / f"decisions_{name}_labels.csv", [{
        "txn_id": t["txn_id"], "is_scam": int(stratum[t["txn_id"]] == "scam"),
        "scam_type": labels.get(t["txn_id"], {}).get("scam_type", ""),
        "pair_id": labels.get(t["txn_id"], {}).get("pair_id", ""),
        "stratum": stratum[t["txn_id"]], "hard_signals": "|".join(hard[t["txn_id"]]),
    } for t in chosen], ["txn_id", "is_scam", "scam_type", "pair_id", "stratum", "hard_signals"])
    counts = {s: sum(v == s for v in stratum.values()) for s in ("scam", "matched_twin", "hard_legit", "random_legit")}
    return {"split_files": split, "users": len(users), "decisions": len(chosen), **counts,
            "scams_by_type": {k: sum(labels[t["txn_id"]]["scam_type"] == k for t in scams)
                              for k in sorted({labels[t["txn_id"]]["scam_type"] for t in scams})}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--val-users", type=int, default=10)
    ap.add_argument("--test-size", type=int, default=400)
    args = ap.parse_args()
    rng = random.Random(f"{args.seed}:eval")
    EVAL.mkdir(exist_ok=True)

    splits = split_users(rng, args.val_users)
    (EVAL / "splits.json").write_text(json.dumps(splits, indent=1))
    summary = {
        "seed": args.seed,
        "val": build("val", "devval", set(splits["val"]), 0, rng),
        "test": build("test", "test", set(splits["test"]), args.test_size, rng),
    }
    (LABELS / "decisions.manifest.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
