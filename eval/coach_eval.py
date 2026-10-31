"""Automatic coaching checks on real decisions, plus a blind rater packet.

    python -m eval.coach_eval --split val --n 30 [--timeout 20]

For each sampled coaching-tier decision: run turn 1 through the live LLM, record validator
verdicts, latency and fallbacks. Also writes a blind A/B packet (contextual Iraqi coaching vs a
generic MSA notice) for at least three Iraqi raters; the LLM never judges its own output.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
from collections import Counter
from pathlib import Path

from coach.catalogue import load_templates, match_family
from coach.llm import LLMClient
from coach.session import CoachRequest, amount_band, next_turn
from coach.templates import build_warning, fill_placeholders, format_iqd
from config import settings
from data.context import GENERATED, decision_context, load_decisions, load_users
from rules.engine import Scorecard, serialize_values
from rules.features import percentile

RESULTS = Path(__file__).resolve().parent / "results"
PACKET = GENERATED / "rater_packet"


def render(turn: dict, fill: dict) -> str:
    parts = [turn.get("concern"), turn.get("next_tactic"), turn.get("next_step"), turn.get("question")]
    return fill_placeholders(" ".join(p for p in parts if p), fill)


async def run(split: str, n: int, timeout: float, seed: int) -> dict:
    sc = Scorecard()
    users = load_users(split)
    cases = []
    for dec in load_decisions(split):
        ctx = decision_context(dec, users[dec["user_id"]])
        a = sc.assess(ctx)
        if a.tier == "coach":
            cases.append((dec, ctx, a))
    rng = random.Random(seed)
    sample = rng.sample(cases, min(n, len(cases)))
    client = LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    rows = []
    for dec, ctx, a in sample:
        fired = [{"id": r.id, "points": r.points, "en": r.en, "ar": r.ar, "values": serialize_values(r.values)}
                 for r in a.fired]
        family = match_family({r["id"]: r["values"] for r in fired}, ctx.draft.reason_code, ctx.draft.note)
        req = CoachRequest(turn=1, fired=fired, family=family, amount_band=amount_band(ctx.draft.amount, ctx.balance,
                                                                                         a.fired_ids))
        res = await next_turn(req, client, timeout)
        fill = {"amount": format_iqd(ctx.draft.amount), "recipient": ctx.draft.recipient_name or ctx.draft.recipient_wallet,
                "first_name": users[dec["user_id"]].raw["user"]["first_name"]}
        rows.append({
            "decision_id": dec["decision_id"], "family": family["id"], "fired": a.fired_ids, "source": res.source,
            "latency_ms": res.latency_ms, "errors": res.errors,
            "template": fill_placeholders(build_warning("coach", fired, family), fill),
            "llm_text": render(res.payload, fill) if res.source == "llm" else "",
        })
        print(f"{dec['decision_id']} {family['id']:<20} {res.source:<8} {res.latency_ms or 0:>7.0f} ms  {res.errors[:3]}")
    return {"rows": rows}


def summarize(rows: list[dict], timeout: float) -> dict:
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    errs = Counter(e.split(":")[0] + (":" + e.split(":")[1] if e.startswith("cited_rules") else "")
                   for r in rows for e in r["errors"])
    llm_ok = sum(r["source"] == "llm" for r in rows)
    return {
        "n": len(rows), "timeout_s": timeout, "llm_shown": llm_ok, "template_fallback": len(rows) - llm_ok,
        "validator_pass_rate": llm_ok / len(rows) if rows else None,
        "latency_ms": {"p50": statistics.median(lat), "p95": percentile(lat, 95)} if lat else None,
        "within_3s": sum(x <= 3000 for x in lat) / len(rows) if rows else None,
        "error_types": dict(errs),
        "reasons_not_from_fired_rules_shown": 0,  # the validator blocks uncited/unfired rules before display
        "uncited_rules_blocked": errs.get("cited_rules:not_fired", 0),
    }


def write_packet(rows: list[dict], seed: int) -> None:
    """Blind A/B pairs: Iraqi contextual coaching vs the generic MSA notice, order randomised."""
    PACKET.mkdir(parents=True, exist_ok=True)
    msa = load_templates()["msa_generic"]
    rng = random.Random(seed)
    with (PACKET / "pairs.csv").open("w", newline="", encoding="utf-8-sig") as f, \
         (PACKET / "key.csv").open("w", newline="", encoding="utf-8-sig") as k:
        wf, wk = csv.writer(f), csv.writer(k)
        wf.writerow(["case", "message_A", "message_B", "clearer (A/B)", "more natural Iraqi (A/B)",
                     "would make me stop (A/B)", "any wrong facts? (A/B/none)", "notes"])
        wk.writerow(["case", "A", "B", "decision_id"])
        for i, r in enumerate(rows, 1):
            ours = r["llm_text"] or r["template"]
            pair = [("contextual", ours), ("msa_generic", msa)]
            rng.shuffle(pair)
            wf.writerow([i, pair[0][1], pair[1][1], "", "", "", "", ""])
            wk.writerow([i, pair[0][0], pair[1][0], r["decision_id"]])


def to_markdown(s: dict, rows: list[dict]) -> str:
    lat = s["latency_ms"] or {}
    lines = [
        "# Coaching auto-checks", "",
        f"{s['n']} coaching-tier decisions, turn 1 through the live model, timeout {s['timeout_s']} s.", "",
        "| Check | Result |", "|---|---|",
        f"| Shown from the model (passed validator in time) | {s['llm_shown']} / {s['n']} |",
        f"| Fell back to the reviewed template | {s['template_fallback']} |",
        f"| Latency p50 / p95 | {lat.get('p50', 0):.0f} ms / {lat.get('p95', 0):.0f} ms |",
        f"| Within the deck's 3 s target | {(s['within_3s'] or 0) * 100:.0f}% |",
        f"| Reasons shown that are not fired rules | {s['reasons_not_from_fired_rules_shown']} (validator-enforced) |",
        f"| Model turns blocked for citing unfired rules | {s['uncited_rules_blocked']} |", "",
        "Validator rejections by type: " + (", ".join(f"{k} ×{v}" for k, v in s["error_types"].items()) or "none"), "",
        "Dialect quality, clarity and usefulness are NOT measured here: they need the blind Iraqi rater study "
        "(packet in data/generated/rater_packet/).", "", "## Samples", ""]
    for r in rows[:6]:
        lines += [f"**{r['decision_id']}** · {r['family']} · fired {' '.join(r['fired'])} · {r['source']}", "",
                  f"> {r['llm_text'] or r['template']}", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["dev", "val", "test"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=settings.coach_timeout_s)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()
    out = asyncio.run(run(args.split, args.n, args.timeout, args.seed))
    s = summarize(out["rows"], args.timeout)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"coach_{args.split}.json").write_text(json.dumps({"summary": s, "rows": out["rows"]}, ensure_ascii=False,
                                                                 indent=1), encoding="utf-8")
    (RESULTS / f"coach_{args.split}.md").write_text(to_markdown(s, out["rows"]), encoding="utf-8")
    write_packet(out["rows"], args.seed)
    print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
