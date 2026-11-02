"""Generate the wording on scam transfers (mule account names and transfer notes) with an LLM.

Only text comes from the LLM; the injector decides when this text is used and every number.
Dev and test use different prompts so the test set does not share the dev set's wording.

Usage:
    python data/scripts/generate_scam_text.py --split devval
    python data/scripts/generate_scam_text.py --split test --prompt data/prompts/scam_text_v2.txt --seed 7731 --temperature 1.0
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from generate_personas import call_llm

DATA = Path(__file__).resolve().parents[1]

SCAM_TYPES = {
    "impersonation": "Caller pretends to be the wallet, a bank or the Central Bank and says the account is at "
                     "risk; the victim must move the money to a 'safe account' now.",
    "fake_exchange": "A fake exchange-company page on social media takes dinar deposits for dollars or "
                     "investment at a good rate, then disappears.",
    "prize_fee": "The victim is told they won a prize, a grant or a bonus, and must pay a fee or "
                 "'activation' to unlock it.",
    "refund_scam": "Money arrives from a stranger, then someone calls or messages saying it was a mistake "
                   "and asks for it back, often to a different account.",
    "hacked_relative": "A relative's or friend's account or number is taken over, or a new number claims to be "
                       "them, asking for urgent help or a loan.",
    # Held back: only in the test set, never used to tune rules.
    "investment": "A 'quick profit' or trading scheme takes several growing payments, showing fake gains "
                  "to get more.",
    "fake_seller": "A seller on a marketplace or Facebook group asks for a deposit or full payment for a "
                   "phone, car part or furniture that never arrives.",
}
HELD_OUT = {"investment", "fake_seller"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["devval", "test"])
    ap.add_argument("--names", type=int, default=12)
    ap.add_argument("--notes", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--url", default="http://localhost:8070")
    ap.add_argument("--model", default="google/gemma-4-31B-it")
    ap.add_argument("--prompt", default=str(DATA / "prompts" / "scam_text_v1.txt"))
    args = ap.parse_args()

    types = {k: v for k, v in SCAM_TYPES.items() if args.split == "test" or k not in HELD_OUT}
    entry = {"type": "object",
             "properties": {"account_names": {"type": "array", "items": {"type": "string"},
                                              "minItems": args.names, "maxItems": args.names},
                            "notes": {"type": "array", "items": {"type": "string"},
                                      "minItems": args.notes, "maxItems": args.notes}},
             "required": ["account_names", "notes"]}
    schema = {"type": "object", "properties": {k: entry for k in types}, "required": list(types)}
    template = Path(args.prompt).read_text(encoding="utf-8")
    prompt = template.format(n_names=args.names, n_notes=args.notes,
                             types="\n".join(f"- {k}: {v}" for k, v in types.items()))

    bank = json.loads(call_llm(args.url, args.model, prompt, schema, args.temperature, args.seed, 8000))
    for k in bank:  # drop duplicates and anything with digits (no amounts in text)
        for field in ("account_names", "notes"):
            seen = []
            for s in bank[k][field]:
                if s.strip() and s not in seen and not any(ch.isdigit() for ch in s):
                    seen.append(s.strip())
            bank[k][field] = seen

    out = DATA / args.split / "scam_text.json"
    out.write_text(json.dumps({
        "model": args.model, "seed": args.seed, "temperature": args.temperature,
        "prompt_file": Path(args.prompt).name,
        "prompt_sha256": hashlib.sha256(template.encode()).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "types": bank,
    }, ensure_ascii=False, indent=1))
    print(f"wrote {out}: " + ", ".join(f"{k} {len(v['account_names'])}/{len(v['notes'])}" for k, v in bank.items()))


if __name__ == "__main__":
    main()
