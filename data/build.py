"""Build users, decisions and (separately) labels for each split.

    python -m data.build [--splits dev val] [--offline]

The test split is built only on request (`--splits test`) and should be built after the rules
are frozen (`python -m eval.freeze`); the build refuses otherwise unless --allow-unfrozen.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from data.context import GENERATED
from data.decisions import build_decisions
from data.personas import PROMPT_SET, SPLITS, load_personas
from data.personas import generate as generate_personas
from data.scripts import generate as generate_scripts
from data.scripts import load_scripts
from data.simulator import Simulator, WalletBook

SEED = 2026
LABEL_DIR = GENERATED / "labels"


def build_split(split: str, offline: bool) -> dict:
    personas = load_personas(split)
    if len(personas) < SPLITS[split]:
        generate_personas([split], offline=offline, force=False)
        personas = load_personas(split)
    book = WalletBook(f"{SEED}-{split}")
    sim = Simulator(SEED, book=book)
    users = [sim.simulate(p) for p in personas]

    out = GENERATED / split
    (out / "users").mkdir(parents=True, exist_ok=True)
    for old in (out / "users").glob("*.json"):
        old.unlink()
    for su in users:
        (out / "users" / f"{su.user['id']}.json").write_text(json.dumps(su.to_json(), ensure_ascii=False),
                                                              encoding="utf-8")

    scripts = load_scripts(PROMPT_SET[split])
    decisions, labels = build_decisions(split, users, scripts, book, SEED, held_out=(split == "test"))
    with (out / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    with (LABEL_DIR / f"{split}.jsonl").open("w", encoding="utf-8") as f:
        for lab in labels:
            f.write(json.dumps(lab, ensure_ascii=False) + "\n")

    summary = {
        "split": split,
        "users": len(users),
        "segments": dict(Counter(su.user["segment"] for su in users)),
        "persona_sources": dict(Counter(p.get("source") for p in personas)),
        "script_sources": {fam: s.get("source") for fam, s in scripts.items()},
        "decisions": len(decisions),
        "scams": sum(lab["is_scam"] for lab in labels),
        "by_family": dict(Counter(lab["family"] for lab in labels if lab["is_scam"])),
        "legit_by_category": dict(Counter(lab["category"] for lab in labels if not lab["is_scam"])),
        "history_txns": sum(len(su.history) for su in users),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["dev", "val"], choices=list(SPLITS))
    ap.add_argument("--offline", action="store_true", help="never call the LLM; use the hand-written pools")
    ap.add_argument("--allow-unfrozen", action="store_true", help="build the test split before freezing rules")
    args = ap.parse_args()
    if "test" in args.splits and not args.allow_unfrozen:
        from eval.freeze import is_frozen

        if not is_frozen():
            raise SystemExit("rules are not frozen: run `python -m eval.freeze` before building the test split")
    generate_scripts(offline=args.offline, force=False)
    for split in args.splits:
        s = build_split(split, args.offline)
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()
