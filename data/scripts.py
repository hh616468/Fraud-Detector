"""Scam script pools: recipient display names and transfer notes per scam family.

Set A (development/validation) and set B (test) come from different prompts, and set B adds two
scam families never used in tuning. Python decides amounts and timing; scripts only supply text.

    python -m data.scripts [--offline] [--force]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data import pools

SCRIPT_DIR = Path(__file__).resolve().parent / "scripts"
DEV_FAMILIES = ["safe_account", "exchange_deposit", "prize_fee", "refund_after_credit", "hacked_relative"]
HELD_OUT_FAMILIES = ["marketplace_deposit", "investment_platform"]
SET_FAMILIES = {"A": DEV_FAMILIES, "B": DEV_FAMILIES + HELD_OUT_FAMILIES}

DESCRIPTIONS = {
    "safe_account": "a caller pretending to be the wallet company, a bank or the central bank says the victim's "
                    "money is at risk and must be moved to a 'safe account' now",
    "exchange_deposit": "a fake currency-exchange company page on Facebook/Telegram asks for a dinar deposit to "
                        "reserve a good dollar rate",
    "prize_fee": "the victim is told they won a prize, grant or gift and must first pay a fee or activation charge",
    "refund_after_credit": "a stranger sends money 'by mistake' and asks the victim to send it back",
    "hacked_relative": "a relative's or friend's hacked account (or a new number using their name) urgently asks "
                       "for money",
    "marketplace_deposit": "a fake online seller asks for a deposit (عربون) before delivering goods that never arrive",
    "investment_platform": "a fake trading/investment platform shows profits and asks for more deposits before "
                           "withdrawals",
}

PROMPTS = {
    "A": {
        "temperature": 0.9,
        "system": "You help build synthetic test data for an Iraqi wallet fraud-warning system. Output one JSON "
                  "object only. Arabic text in Iraqi style.",
        "extra": "",
    },
    "B": {
        "temperature": 1.05,
        "system": "أنت تكتب بيانات اختبار لنظام يكشف احتيال المحافظ الإلكترونية بالعراق. الجواب JSON واحد فقط.",
        "extra": "Make the wording varied and less obvious than typical examples: in about half of the notes avoid "
                 "the words رسوم، جائزة، عربون، حساب آمن، and mix in some English or Arabizi names.",
    },
}


def _user_prompt(family: str, set_name: str) -> str:
    names_rule = ("Return an empty names list (the scam reuses a saved contact's name)."
                  if family == "hacked_relative" else
                  "names: 10 recipient display names the scammer's wallet might show (mix of official-sounding "
                  "names and ordinary personal names).")
    return (
        f"Scam pattern: {DESCRIPTIONS[family]}.\n{names_rule}\n"
        "notes: 10 short transfer notes (at most 7 words, Arabic) a victim might be told to type, include 2 empty "
        f"strings.\n{PROMPTS[set_name]['extra']}\nNo digits, no phone numbers, no links. Keys: names, notes."
    )


def _clean(items, max_len: int) -> list[str]:
    out = []
    for x in items or []:
        if isinstance(x, str) and len(x) <= max_len and not any(ch.isdigit() for ch in x) and "http" not in x:
            out.append(x.strip())
    return list(dict.fromkeys(out))


def generate(offline: bool, force: bool) -> None:
    use_llm = False
    if not offline:
        from data.llm_gen import reachable

        use_llm = reachable()
        if not use_llm:
            print("LLM unreachable: using offline script pools only")
    for set_name, families in SET_FAMILIES.items():
        (SCRIPT_DIR / set_name).mkdir(parents=True, exist_ok=True)
        for fam in families:
            path = SCRIPT_DIR / set_name / f"{fam}.json"
            if path.exists() and not force and (json.loads(path.read_text(encoding="utf-8")).get("source") == "llm"
                                                or not use_llm):
                continue
            base = pools.SCAM_SCRIPTS[set_name][fam]
            pool = {"names": list(base["names"]), "notes": list(base["notes"]), "source": "offline"}
            if use_llm:
                from data.llm_gen import chat_json

                try:
                    raw = chat_json(PROMPTS[set_name]["system"], _user_prompt(fam, set_name),
                                    temperature=PROMPTS[set_name]["temperature"], max_tokens=900)
                    pool["names"] = list(dict.fromkeys(pool["names"] + _clean(raw.get("names"), 50)))
                    pool["notes"] = list(dict.fromkeys(pool["notes"] + _clean(raw.get("notes"), 60)))
                    pool["source"] = "llm"
                except Exception as exc:  # noqa: BLE001
                    print(f"  {set_name}/{fam}: LLM failed ({type(exc).__name__}), offline pool kept")
            if fam == "hacked_relative":
                pool["names"] = []
            path.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {set_name}/{fam}: {len(pool['names'])} names, {len(pool['notes'])} notes [{pool['source']}]")


def load_scripts(set_name: str) -> dict[str, dict]:
    out = {}
    for fam in SET_FAMILIES[set_name]:
        path = SCRIPT_DIR / set_name / f"{fam}.json"
        if path.exists():
            out[fam] = json.loads(path.read_text(encoding="utf-8"))
        else:
            base = pools.SCAM_SCRIPTS[set_name][fam]
            out[fam] = {"names": list(base["names"]), "notes": list(base["notes"]), "source": "offline"}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    generate(a.offline, a.force)
