"""Check generated data with code, and export a sample for manual review.

    python -m data.validate [--splits dev val test]

Exits non-zero if any hard check fails. Writes data/generated/review_sample.md for a human to read.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import Counter
from datetime import datetime

from data.context import GENERATED, decision_context, load_decisions, load_users
from data.decisions import LEGIT_MIX
from data.personas import SPLITS


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def check_split(split: str) -> tuple[list[str], dict]:
    errors: list[str] = []
    users_dir = GENERATED / split / "users"
    raws = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(users_dir.glob("*.json"))}
    if len(raws) != SPLITS[split]:
        errors.append(f"{split}: expected {SPLITS[split]} users, found {len(raws)}")
    stats = {"monthly_out": [], "median_out": [], "segments": Counter()}
    for uid, u in raws.items():
        hist = u["history"]
        ts = [_dt(t["ts"]) for t in hist]
        if ts != sorted(ts):
            errors.append(f"{uid}: history not sorted")
        start, end = _dt(u["sim_start"]), _dt(u["sim_end"])
        if ts and (ts[0] < start or ts[-1] >= end):
            errors.append(f"{uid}: transactions outside the simulation window")
        bal = u["start_balance"]
        for t in hist:
            if t["amount"] <= 0 or t["amount"] % 250:
                errors.append(f"{uid}: bad amount {t['amount']}")
                break
            bal += t["amount"] if t["direction"] == "in" else -t["amount"]
            if bal < 0:
                errors.append(f"{uid}: balance negative at {t['ts']}")
                break
        params = u["params"]
        for t in hist:
            if t["kind"] in ("salary", "pension") and _dt(t["ts"]).day != params.get("pay_day"):
                errors.append(f"{uid}: income off its pay day ({t['ts']})")
                break
        wallets = [c["wallet"] for c in u["contacts"]]
        if len(wallets) != len(set(wallets)):
            errors.append(f"{uid}: duplicate contact wallets")
        outs = [t for t in hist if t["direction"] == "out"]
        months = max(1.0, (end - start).days / 30)
        stats["monthly_out"].append(len(outs) / months)
        if outs:
            stats["median_out"].append(statistics.median(t["amount"] for t in outs))
        stats["segments"][u["user"]["segment"]] += 1

    decisions = load_decisions(split)
    labels = [json.loads(x) for x in (GENERATED / "labels" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if x]
    if {d["decision_id"] for d in decisions} != {lab["decision_id"] for lab in labels}:
        errors.append(f"{split}: decisions and labels do not match one to one")
    for d in decisions:
        leaked = {"is_scam", "family", "category", "pair_id"} & (set(d) | set(d["draft"]) | set(d["setup"]))
        if leaked:
            errors.append(f"{d['decision_id']}: label fields in the decision record: {leaked}")
            break
    users = load_users(split)
    for d in decisions:
        ctx = decision_context(d, users[d["user_id"]])
        if ctx.draft.amount > ctx.balance:
            errors.append(f"{d['decision_id']}: amount {ctx.draft.amount} exceeds balance {ctx.balance}")
        if any(t.ts >= ctx.draft.ts for t in ctx.history):
            errors.append(f"{d['decision_id']}: context contains a transaction at/after the decision")
    scale = SPLITS[split] / 20
    cats = Counter(lab["category"] for lab in labels if not lab["is_scam"])
    expected_legit = sum(round(v * scale) for v in LEGIT_MIX.values())
    if sum(cats.values()) != expected_legit:
        errors.append(f"{split}: {sum(cats.values())} legitimate decisions, expected {expected_legit}")
    summary = {
        "users": len(raws),
        "segments": dict(stats["segments"]),
        "outgoing_per_month_median": round(statistics.median(stats["monthly_out"]), 1),
        "median_outgoing_amount_iqd": int(statistics.median(stats["median_out"])),
        "decisions": len(decisions),
        "scams": sum(lab["is_scam"] for lab in labels),
        "legit_categories": dict(cats),
        "scam_families": dict(Counter(lab["family"] for lab in labels if lab["is_scam"])),
    }
    return errors, summary


def check_disjoint(splits: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    errors = []
    for split in splits:
        for f in (GENERATED / split / "users").glob("*.json"):
            if f.stem in seen:
                errors.append(f"user {f.stem} appears in {seen[f.stem]} and {split}")
            seen[f.stem] = split
    return errors


def review_sample(split: str, n_users: int = 4, n_decisions: int = 12, seed: int = 11) -> str:
    rng = random.Random(seed)
    users = load_users(split)
    decisions = load_decisions(split)
    labels = {x["decision_id"]: x for x in map(json.loads, (GENERATED / "labels" / f"{split}.jsonl")
                                               .read_text(encoding="utf-8").splitlines()) if x}
    lines = [f"## {split}: users"]
    for uid in rng.sample(sorted(users), min(n_users, len(users))):
        u = users[uid].raw
        p = u["persona"]
        lines += ["", f"### {uid} · {p['first_name']} {p.get('last_name', '')} · {p['segment']} · {p.get('city', '')} · "
                      f"{p.get('job', '')} · source={p.get('source')}",
                  f"habits: {', '.join(p.get('habits', []))} · contacts: "
                  + ", ".join(f"{c['name']} ({c['relation']})" for c in u["contacts"]),
                  "", "| time | dir | counterparty | amount | kind | note |", "|---|---|---|---|---|---|"]
        for t in u["history"][-15:]:
            lines.append(f"| {t['ts'][:16]} | {t['direction']} | {t['counterparty_name']} | {t['amount']:,} | {t['kind']} | {t['note']} |")
    lines += ["", f"## {split}: decisions (labels shown for review only)", "",
              "| id | label | user | amount | recipient | note | reason | call | setup |", "|---|---|---|---|---|---|---|---|---|"]
    for d in rng.sample(decisions, min(n_decisions, len(decisions))):
        lab = labels[d["decision_id"]]
        tag = f"SCAM:{lab['family']}" if lab["is_scam"] else lab["category"]
        dr = d["draft"]
        setup = ", ".join([e["kind"] for e in d["setup"]["device_events"]] +
                          [f"{t['direction']} {t['amount']:,}" for t in d["setup"]["extra_history"]])
        lines.append(f"| {d['decision_id']} | {tag} | {d['user_id']} | {dr['amount']:,} | {dr['recipient_name']} | {dr['note']} "
                     f"| {dr['reason_code'] or ''} | {'yes' if dr['call_active'] else ''} | {setup} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["dev", "val"])
    args = ap.parse_args()
    all_errors = check_disjoint(args.splits)
    report = ["# Manual review sample", "",
              "Read these by hand: do the histories look like plausible Iraqi wallet users, and do the scam and "
              "legitimate decisions read as intended?", ""]
    for split in args.splits:
        errors, summary = check_split(split)
        all_errors += errors
        print(f"{split}: {'OK' if not errors else f'{len(errors)} problems'}  {json.dumps(summary, ensure_ascii=False)}")
        report += [review_sample(split), ""]
    (GENERATED / "review_sample.md").write_text("\n".join(report), encoding="utf-8")
    print(f"review sample: {GENERATED / 'review_sample.md'}")
    for e in all_errors[:40]:
        print("  -", e)
    sys.exit(1 if all_errors else 0)


if __name__ == "__main__":
    main()
