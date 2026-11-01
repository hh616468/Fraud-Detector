"""Score every decision of a split and write the results table.

    python -m eval.run_eval --split dev        # tuning
    python -m eval.run_eval --split val        # check before freezing
    python -m eval.run_eval --split test       # once, after `python -m eval.freeze`

Outputs eval/results/results_<split>_<version>.md and .json, plus predictions_<split>.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from data.context import GENERATED, decision_context, load_decisions, load_users
from eval.freeze import frozen_record, is_frozen
from eval.metrics import clustered_bootstrap, confusion, precision_at_prevalence
from rules.baselines import BASELINES
from rules.engine import Scorecard
from rules.features import percentile

RESULTS = Path(__file__).resolve().parent / "results"
TARGETS = {"precision": (">=", 0.90), "recall": (">=", 0.80), "fpr": ("<=", 0.02)}
PREVALENCES = [0.001, 0.005, 0.01, 0.05]
DETECTORS = {"scorecard_warn": "Scorecard, any warning (score ≥ 4 or override)",
             "scorecard_coach": "Scorecard, coaching tier only (score ≥ 6 or override)",
             "flag_all_new_payees": "Baseline: warn on every new payee"}


def load_labels(split: str) -> dict[str, dict]:
    path = GENERATED / "labels" / f"{split}.jsonl"
    return {lab["decision_id"]: lab for lab in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if lab}


def score_split(split: str, scorecard: Scorecard) -> list[dict]:
    users = load_users(split)
    labels = load_labels(split)
    rows = []
    for dec in load_decisions(split):
        ctx = decision_context(dec, users[dec["user_id"]])
        a = scorecard.assess(ctx)
        lab = labels[dec["decision_id"]]
        rows.append({
            "decision_id": dec["decision_id"], "user_id": dec["user_id"], "is_scam": lab["is_scam"],
            "family": lab["family"], "category": lab["category"], "held_out_family": lab["held_out_family"],
            "pair_id": lab["pair_id"], "score": a.score, "tier": a.tier, "override": a.override,
            "fired": a.fired_ids, "latency_ms": a.latency_ms,
            "scorecard_warn": a.tier in ("nudge", "coach"), "scorecard_coach": a.tier == "coach",
            **{name: fn(ctx) for name, fn in BASELINES.items()},
        })
    return rows


def _fmt(v, pct=True) -> str:
    if v is None:
        return "–"
    return f"{v * 100:.1f}%" if pct else f"{v:.3f}"


def _ci(ci) -> str:
    return f"[{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if ci else "–"


def summarize(rows: list[dict], split: str, scorecard: Scorecard) -> dict:
    out: dict = {"split": split, "rules_version": scorecard.version, "fingerprint": scorecard.fingerprint,
                 "frozen": is_frozen(), "frozen_record": frozen_record(), "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "n": len(rows), "n_scam": sum(r["is_scam"] for r in rows), "detectors": {}}
    for key in DETECTORS:
        c = confusion(rows, key)
        out["detectors"][key] = {**c.as_dict(), "bootstrap": clustered_bootstrap(rows, key),
                                 "at_prevalence": {str(p): precision_at_prevalence(c.recall or 0, c.fpr or 0, p)
                                                   for p in PREVALENCES}}
    fam_rows: dict[str, list[dict]] = defaultdict(list)
    cat_rows: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        (fam_rows[r["family"]] if r["is_scam"] else cat_rows[r["category"]]).append(r)
    out["by_family"] = {
        f: {"n": len(rs), "held_out": rs[0]["held_out_family"],
            **{k: sum(r[k] for r in rs) / len(rs) for k in DETECTORS}} for f, rs in sorted(fam_rows.items())}
    out["by_legit_category"] = {
        c: {"n": len(rs), **{k: sum(r[k] for r in rs) / len(rs) for k in DETECTORS}} for c, rs in sorted(cat_rows.items())}
    tiers = Counter((r["is_scam"], r["tier"]) for r in rows)
    out["tiers"] = {f"{'scam' if s else 'legit'}:{t}": n for (s, t), n in sorted(tiers.items())}
    scams = [r for r in rows if r["is_scam"]]
    legit = [r for r in rows if not r["is_scam"]]
    rule_ids = [r["id"] for r in scorecard.rules]
    out["rules"] = {
        rid: {"scam_rate": sum(rid in r["fired"] for r in scams) / max(1, len(scams)),
              "legit_rate": sum(rid in r["fired"] for r in legit) / max(1, len(legit))}
        for rid in rule_ids
    }
    pairs: dict[str, dict] = defaultdict(dict)
    for r in rows:
        if r["pair_id"]:
            pairs[r["pair_id"]]["scam" if r["is_scam"] else "twin"] = r["scorecard_warn"]
    out["matched_pairs"] = dict(Counter(
        "both warned" if p.get("scam") and p.get("twin") else "scam only" if p.get("scam") else
        "twin only" if p.get("twin") else "neither" for p in pairs.values() if len(p) == 2))
    lat = [r["latency_ms"] for r in rows]
    out["latency_ms"] = {"p50": statistics.median(lat), "p95": percentile(lat, 95), "max": max(lat)}
    return out


def to_markdown(s: dict) -> str:
    d = s["detectors"]
    lines = [
        f"# Detection results: {s['split']} split, rules {s['rules_version']}",
        "",
        f"Generated {s['generated_at']} · rules fingerprint `{s['fingerprint']}` · frozen: **{s['frozen']}**"
        + (f" (frozen at {s['frozen_record']['frozen_at']})" if s.get("frozen_record") else ""),
        "",
        f"{s['n']} held-out decisions, {s['n_scam']} scams and {s['n'] - s['n_scam']} legitimate. All data is synthetic; "
        "these numbers say nothing about production effectiveness.",
        "",
        "## Headline",
        "",
        "| Detector | Precision [95% CI] | Recall [95% CI] | FPR [95% CI] | TP | FP | TN | FN |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, label in DETECTORS.items():
        m = d[key]
        lines.append(f"| {label} | {_fmt(m['precision'])} {_ci(m['precision_ci'])} | {_fmt(m['recall'])} {_ci(m['recall_ci'])} "
                     f"| {_fmt(m['fpr'])} {_ci(m['fpr_ci'])} | {m['tp']} | {m['fp']} | {m['tn']} | {m['fn']} |")
    lines += ["", "Intervals are Wilson score intervals. User-clustered bootstrap (1,000 resamples of users):", "",
              "| Detector | Precision | Recall | FPR |", "|---|---|---|---|"]
    for key, label in DETECTORS.items():
        b = d[key]["bootstrap"]
        lines.append(f"| {label} | {_ci(b['precision'])} | {_ci(b['recall'])} | {_ci(b['fpr'])} |")

    w = d["scorecard_warn"]
    lines += ["", "## Against the slide targets (targets, not claims)", "", "| Metric | Target | Measured (any warning) | Met? |",
              "|---|---|---|---|"]
    for k, (op, t) in TARGETS.items():
        v = w[k]
        met = v is not None and (v >= t if op == ">=" else v <= t)
        lines.append(f"| {k} | {op} {t * 100:.0f}% | {_fmt(v)} | {'yes' if met else 'no'} |")

    lines += ["", "## Recall by scam family", "", "| Family | n | Held out | Any warning | Coaching tier | Baseline |",
              "|---|---|---|---|---|---|"]
    for f, m in s["by_family"].items():
        lines.append(f"| {f} | {m['n']} | {'yes' if m['held_out'] else ''} | {_fmt(m['scorecard_warn'])} | "
                     f"{_fmt(m['scorecard_coach'])} | {_fmt(m['flag_all_new_payees'])} |")
    lines += ["", "## Warning rate on legitimate transfers, by type", "",
              "| Category | n | Any warning | Coaching tier | Baseline |", "|---|---|---|---|---|"]
    for c, m in s["by_legit_category"].items():
        lines.append(f"| {c} | {m['n']} | {_fmt(m['scorecard_warn'])} | {_fmt(m['scorecard_coach'])} | "
                     f"{_fmt(m['flag_all_new_payees'])} |")
    lines += ["", "## Precision at assumed prevalence", "",
              "The test set is enriched (25% scams), which flatters precision. Same recall and FPR at realistic rates:", "",
              "| Detector | " + " | ".join(f"{p * 100:g}%" for p in PREVALENCES) + " |", "|---|" + "---|" * len(PREVALENCES)]
    for key, label in DETECTORS.items():
        ap = d[key]["at_prevalence"]
        lines.append(f"| {label} | " + " | ".join(_fmt(ap[str(p)]) for p in PREVALENCES) + " |")
    lines += ["", "Real Iraqi scam prevalence is unknown; this table is arithmetic, not an estimate.", "",
              "## Rule firing rates", "", "| Rule | Scams | Legitimate |", "|---|---|---|"]
    for rid, m in s["rules"].items():
        lines.append(f"| {rid} | {_fmt(m['scam_rate'])} | {_fmt(m['legit_rate'])} |")
    lines += ["", "## Tiers", "", "| Truth:tier | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in s["tiers"].items()]
    if s["matched_pairs"]:
        lines += ["", "## Matched pairs (same user, time, amount, call flag; different intent)", "",
                  "| Outcome | Pairs |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(s["matched_pairs"].items())]
        lines += ["", "A transaction-only detector cannot separate pairs that look the same; this is its ceiling."]
    lat = s["latency_ms"]
    lines += ["", "## Assessment latency", "", f"p50 {lat['p50']:.2f} ms · p95 {lat['p95']:.2f} ms · max {lat['max']:.2f} ms "
              "(scorecard only, in-process; the deck's budget is 500 ms for the warning)."]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "val", "test"])
    ap.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args()
    if args.split == "test" and not is_frozen() and not args.allow_unfrozen:
        raise SystemExit("rules changed since freezing (or were never frozen); refusing to touch the test split")
    sc = Scorecard()
    rows = score_split(args.split, sc)
    s = summarize(rows, args.split, sc)
    RESULTS.mkdir(parents=True, exist_ok=True)
    stem = f"results_{args.split}_{sc.version}"
    (RESULTS / f"{stem}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (RESULTS / f"{stem}.md").write_text(to_markdown(s), encoding="utf-8")
    with (RESULTS / f"predictions_{args.split}.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["decision_id", "user_id", "is_scam", "family", "category", "score", "tier", "fired", "baseline"])
        for r in rows:
            wr.writerow([r["decision_id"], r["user_id"], int(r["is_scam"]), r["family"] or "", r["category"], r["score"],
                         r["tier"], " ".join(r["fired"]), int(r["flag_all_new_payees"])])
    w = s["detectors"]["scorecard_warn"]
    b = s["detectors"]["flag_all_new_payees"]
    print(f"{args.split}: n={s['n']} scams={s['n_scam']}  scorecard P={_fmt(w['precision'])} R={_fmt(w['recall'])} "
          f"FPR={_fmt(w['fpr'])}  | baseline P={_fmt(b['precision'])} R={_fmt(b['recall'])} FPR={_fmt(b['fpr'])}")
    print(f"wrote {RESULTS / (stem + '.md')}")


if __name__ == "__main__":
    main()
