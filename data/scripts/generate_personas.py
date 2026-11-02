"""Generate synthetic Iraqi wallet personas with an LLM.

Python decides the mix (segment, city, gender, age, income source) per slot with a
fixed seed; the LLM fills in names, contacts and habits. The LLM never writes amounts.

Usage:
    python data/scripts/generate_personas.py --count 40 --prefix D --out data/devval/personas.jsonl
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[1]

INCOME_BANDS = ["<300k", "300k-500k", "500k-750k", "750k-1M", "1M-1.5M", "1.5M-2.5M", ">2.5M"]
AMOUNT_BANDS = ["5k-25k", "25k-100k", "100k-250k", "250k-500k", "500k-1M", ">1M"]
# Money amounts in free text ("50k", "25,000", "10000", "5 dinars"); small counts like "2 times" are fine.
MONEY = re.compile(r"\d[\d,.]*\s*(k\b|K\b|IQD|dinar|دينار|الف|ألف)|\d{4,}|\d{1,3}(,\d{3})+", re.I)

# Contact-name words that pin down the relation, checked after normalising alef forms.
NAME_RELATION = {
    "child": {"ابني", "ابنتي", "بنتي"}, "sibling": {"اختي", "اخوي", "اخي"},
    "parent": {"امي", "ابوي", "بابا", "ماما", "والدي", "والدتي"}, "spouse": {"زوجي", "زوجتي"},
    "friend": {"صديقي", "صديقتي", "صاحبي"},
}
BUSINESS_WORDS = ["محل", "سوق", "مطعم", "كافيه", "كافتيريا", "صيدلية", "مولدة", "كراج", "ماركت", "مكتب",
                  "مدرسة", "مكتبة", "شركة", "كارت", "مخبز", "خباز", "حلاق", "دكتور", "عيادة", "انترنت",
                  "فرن", "مقاول"]
PERSON_RELATIONS = {"parent", "child", "sibling", "spouse", "relative", "friend", "colleague"}

BAND_LOW = {"5k-25k": 5_000, "25k-100k": 25_000, "100k-250k": 100_000,
            "250k-500k": 250_000, "500k-1M": 500_000, ">1M": 1_000_000}
INCOME_HIGH = {"<300k": 300_000, "300k-500k": 500_000, "500k-750k": 750_000, "750k-1M": 1_000_000,
               "1M-1.5M": 1_500_000, "1.5M-2.5M": 2_500_000, ">2.5M": 10_000_000}
# Monthly income band weights per source. Our assumptions, not official statistics.
INCOME_BY_SOURCE = {
    "pension": {"<300k": .15, "300k-500k": .35, "500k-750k": .3, "750k-1M": .15, "1M-1.5M": .05},
    "gov_salary": {"500k-750k": .25, "750k-1M": .35, "1M-1.5M": .3, "1.5M-2.5M": .1},
    "private_salary": {"300k-500k": .25, "500k-750k": .35, "750k-1M": .25, "1M-1.5M": .1,
                       "1.5M-2.5M": .05},
    "daily_work": {"<300k": .3, "300k-500k": .45, "500k-750k": .25},
    "family_support": {"<300k": .6, "300k-500k": .35, "500k-750k": .05},
    "freelance": {"<300k": .3, "300k-500k": .35, "500k-750k": .2, "750k-1M": .15},
    "none": {"<300k": 1.0},
}
RELATIONS = ["parent", "child", "sibling", "spouse", "relative", "friend", "colleague",
             "landlord", "employer", "shop", "service", "customer"]
FREQUENCIES = ["weekly", "monthly", "occasional", "rare"]
BILLS = ["generator", "internet", "phone_topup", "school_fees", "rent", "installment", "water_electricity"]

# Rough population weights; Baghdad dominates.
CITIES = {"Baghdad": 30, "Basra": 10, "Mosul": 9, "Erbil": 7, "Najaf": 6, "Karbala": 5,
          "Sulaymaniyah": 5, "Hillah": 5, "Nasiriyah": 5, "Kirkuk": 5, "Ramadi": 4,
          "Diwaniyah": 3, "Kut": 3, "Duhok": 3}

SEGMENTS = {
    "retiree": {"age": (58, 78), "income": {"pension": 1.0}},
    "salaried": {"age": (24, 60), "income": {"gov_salary": 0.65, "private_salary": 0.35}},
    "first_time": {"age": (17, 55), "income": {"daily_work": 0.35, "family_support": 0.3,
                                               "freelance": 0.15, "none": 0.2}},
}
SEGMENT_MIX = ["retiree"] * 3 + ["salaried"] * 4 + ["first_time"] * 3

CONTACT = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "relation": {"enum": RELATIONS},
        "direction": {"enum": ["send", "receive", "both"]},
        "frequency": {"enum": FREQUENCIES},
        "amount_band": {"enum": AMOUNT_BANDS},
    },
    "required": ["name", "relation", "direction", "frequency", "amount_band"],
}
PERSONA = {
    "type": "object",
    "properties": {
        "slot": {"type": "integer"},
        "name": {"type": "string"},
        "job": {"type": "string"},
        "family": {"type": "string"},
        "phone_literacy": {"enum": ["low", "medium", "high"]},
        "income_day": {"type": ["integer", "null"]},
        "typical_transfer_band": {"enum": AMOUNT_BANDS},
        "active_hours": {
            "type": "object",
            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["start", "end"],
        },
        "contacts": {"type": "array", "items": CONTACT, "minItems": 5, "maxItems": 12},
        "bills": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"enum": BILLS}, "frequency": {"enum": FREQUENCIES}},
            "required": ["kind", "frequency"]}},
        "habits": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
        "ramadan_eid": {"type": "string"},
        "scam_exposure": {"type": "string"},
    },
    "required": ["slot", "name", "job", "family", "phone_literacy", "income_day",
                 "typical_transfer_band", "active_hours", "contacts", "bills", "habits",
                 "ramadan_eid", "scam_exposure"],
}


def make_slots(count, rng):
    slots = []
    for i in range(count):
        segment = SEGMENT_MIX[i % len(SEGMENT_MIX)]
        spec = SEGMENTS[segment]
        sources, weights = zip(*spec["income"].items())
        source = rng.choices(sources, weights=weights)[0]
        bands, band_weights = zip(*INCOME_BY_SOURCE[source].items())
        slots.append({
            "segment": segment,
            "city": rng.choices(list(CITIES), weights=list(CITIES.values()))[0],
            "gender": rng.choice(["male", "female"]),
            "age": rng.randint(*spec["age"]),
            "income_source": source,
            "income_band": rng.choices(bands, weights=band_weights)[0],
        })
    rng.shuffle(slots)
    return slots


def call_llm(url, model, prompt, schema, temperature, seed, max_tokens):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "personas", "schema": schema}},
    }
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def check(p, slot):
    """Reject personas that break rules the schema can't express."""
    fixed_day = slot["income_source"] in ("gov_salary", "private_salary", "pension")
    if fixed_day != (p["income_day"] is not None):
        return "income_day does not match income_source"
    if p["income_day"] is not None and not 1 <= p["income_day"] <= 28:
        return "income_day out of range"
    h = p["active_hours"]
    if not (0 <= h["start"] <= 23 and 0 <= h["end"] <= 24):
        return "active_hours out of range"
    text = json.dumps([p["habits"], p["ramadan_eid"], p["scam_exposure"]], ensure_ascii=False)
    if MONEY.search(text):
        return f"free text contains an amount: {MONEY.search(text).group()!r}"
    for c in p["contacts"]:
        name = re.sub("[أإآ]", "ا", c["name"])
        words = set(name.split())
        # "ابن اختي", "زوج اختي", "مرت اخوي" are nephews and in-laws: any relation word is fine.
        for relation, keys in NAME_RELATION.items() if not words & {"ابن", "ابنة", "بنت", "زوج", "مرت"} else []:
            if words & keys and c["relation"] != relation:
                return f"contact {c['name']!r} should be {relation}, not {c['relation']}"
        if c["relation"] in PERSON_RELATIONS and any(w in name for w in BUSINESS_WORDS):
            return f"contact {c['name']!r} is a business, not {c['relation']}"
    # Regular outgoing transfers (at their band's lower bound) must fit inside the income band's top.
    if slot["income_source"] != "none":
        per_month = {"weekly": 4, "monthly": 1}
        floor = sum(BAND_LOW[c["amount_band"]] * per_month[c["frequency"]] for c in p["contacts"]
                    if c["direction"] != "receive" and c["frequency"] in per_month)
        if floor > INCOME_HIGH[slot["income_band"]]:
            return f"regular transfers ({floor:,} IQD/month) exceed income band {slot['income_band']}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--prefix", default="D", help="user_id prefix, e.g. D for dev/val, T for test")
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--url", default="http://localhost:8070")
    ap.add_argument("--model", default="google/gemma-4-31B-it")
    ap.add_argument("--prompt", default=str(DATA / "prompts" / "persona_v1.txt"))
    ap.add_argument("--out", default=str(DATA / "devval" / "personas.jsonl"))
    ap.add_argument("--avoid-names-from", action="append", default=[],
                    help="persona file whose names must not be reused (e.g. dev personas for the test set)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    template = Path(args.prompt).read_text(encoding="utf-8")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pending = make_slots(args.count, rng)
    personas, used_names = [], []
    for path in args.avoid_names_from:
        used_names += [json.loads(line)["name"] for line in open(path, encoding="utf-8")]

    # Accepted personas are appended as they arrive; rejected slots go back in the queue.
    if out.exists() and out.stat().st_size:
        out.rename(out.with_suffix(".jsonl.bak"))
        print(f"moved previous output to {out.with_suffix('.jsonl.bak')}")
    out.write_text("")
    calls = 0
    while pending:
        if calls >= 4 * args.count // args.batch + 10:
            sys.exit(f"too many failed calls; {len(pending)} slots left")
        batch, pending = pending[:args.batch], pending[args.batch:]
        schema = {"type": "object",
                  "properties": {"users": {"type": "array", "items": PERSONA,
                                           "minItems": len(batch), "maxItems": len(batch)}},
                  "required": ["users"]}
        slot_text = "\n".join(
            f"Slot {k}: segment={s['segment']}, city={s['city']}, gender={s['gender']}, "
            f"age={s['age']}, income_source={s['income_source']}, "
            f"monthly_income_band={s['income_band']} IQD" for k, s in enumerate(batch))
        prompt = template.format(n=len(batch), slots=slot_text,
                                 used_names=", ".join(used_names) or "(none yet)")
        t0 = time.time()
        calls += 1
        try:
            users = json.loads(call_llm(args.url, args.model, prompt, schema, args.temperature,
                                        args.seed + calls, 1800 * len(batch)))["users"]
        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionRefusedError):
                sys.exit(f"LLM server at {args.url} refused the connection; is it running?")
            print(f"  call {calls} failed after {time.time() - t0:.0f}s: {e}", file=sys.stderr)
            pending += batch
            continue
        except Exception as e:  # noqa: BLE001 - retry on any LLM/parse failure
            print(f"  call {calls} failed after {time.time() - t0:.0f}s: {e}", file=sys.stderr)
            pending += batch
            continue

        accepted = 0
        with out.open("a", encoding="utf-8") as f:
            for u, s in zip(users, batch):
                if err := check(u, s):
                    print(f"  rejected {s['segment']}/{s['city']}: {err}", file=sys.stderr)
                    pending.append(s)
                    continue
                u.pop("slot", None)
                p = {"user_id": f"{args.prefix}{len(personas) + 1:03d}", **s, **u}
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
                personas.append(p)
                used_names.append(u["name"])
                accepted += 1
        print(f"call {calls}: {accepted}/{len(batch)} accepted in {time.time() - t0:.0f}s "
              f"({len(personas)}/{args.count} done)", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model, "url": args.url, "seed": args.seed,
        "temperature": args.temperature, "count": len(personas),
        "prompt_file": Path(args.prompt).name,
        "prompt_sha256": hashlib.sha256(template.encode()).hexdigest(),
        "avoided_names_from": [Path(p).name for p in args.avoid_names_from],
    }
    out.with_name(out.stem + ".manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(personas)} personas to {out}")


if __name__ == "__main__":
    main()
