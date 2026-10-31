"""Generate the 60 synthetic personas (30 dev / 10 val / 20 test).

Python fixes each slot's segment, city, gender, age band, income band and active period, so the
mix is controlled. The LLM only writes names, job, habits and contacts; it writes no numbers.
Dev/val use prompt set A, test uses prompt set B (different framing, example and temperature).

    python -m data.personas [--splits dev val test] [--offline] [--force]
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from data import pools
from data.simulator import rng_for

ROOT = Path(__file__).resolve().parent
PERSONA_DIR = ROOT / "personas"
SPLITS = {"dev": 30, "val": 10, "test": 20}
PREFIX = {"dev": "d", "val": "v", "test": "t"}
SEGMENT_MIX = {
    "dev": {"retiree": 10, "salaried": 12, "first_time": 8},
    "val": {"retiree": 3, "salaried": 4, "first_time": 3},
    "test": {"retiree": 7, "salaried": 7, "first_time": 6},
}
PROMPT_SET = {"dev": "A", "val": "A", "test": "B"}
SEED = 2026

REQUIRED_CONTACT_FOR_HABIT = {"rent": "landlord", "generator_fees": "generator", "savings_group": "savings_group"}

PROMPTS = {
    "A": {
        "temperature": 0.8,
        "system": (
            "You create realistic but entirely fictional Iraqi mobile-wallet users for testing a fraud-warning "
            "system. Never describe real people. Write names in Arabic as Iraqis write them. Answer with one JSON "
            "object only."
        ),
        "example": {
            "first_name": "كاظم", "last_name": "الساعدي", "job": "معلم متقاعد",
            "habits": ["family_support", "generator_fees", "bills", "religious_donations"],
            "contacts": [{"name": "حيدر كاظم", "relation": "son"}, {"name": "أم حيدر", "relation": "spouse"},
                         {"name": "أبو سجاد المولدة", "relation": "generator"}, {"name": "سعد جبار", "relation": "friend"}],
            "tech_comfort": "low",
            "bio": "متقاعد من التعليم، يستخدم المحفظة حتى يدز لابنه ويدفع المولدة.",
        },
    },
    "B": {
        "temperature": 1.0,
        "system": (
            "أنت كاتب قصص من البصرة تكتب بطاقات شخصيات خيالية لمستخدمي محافظ إلكترونية بالعراق، لاختبار نظام "
            "تحذير من الاحتيال. الشخصيات خيالية تماماً. اكتب الأسماء بالعربي. الجواب كائن JSON واحد فقط."
        ),
        "example": {
            "first_name": "رقية", "last_name": "اللامي", "job": "موظفة بالصحة",
            "habits": ["rent", "online_shopping", "phone_topups", "savings_group"],
            "contacts": [{"name": "الحاج مهدي", "relation": "landlord"}, {"name": "نور", "relation": "sister"},
                         {"name": "جمعية الشعبة", "relation": "savings_group"}, {"name": "ماما", "relation": "mother"}],
            "tech_comfort": "high",
            "bio": "تسكن إيجار، تتسوق من الانستغرام وتشترك بجمعية وية زميلاتها.",
        },
    },
}


def slots(split: str) -> list[dict]:
    rng = rng_for(SEED, "slots", split)
    segs = [s for s, n in SEGMENT_MIX[split].items() for _ in range(n)]
    rng.shuffle(segs)
    out = []
    for i, seg in enumerate(segs, 1):
        out.append({
            "id": f"{PREFIX[split]}{i:03d}",
            "split": split,
            "segment": seg,
            "gender": rng.choice(["m", "f"]),
            "age_band": rng.choice(pools.AGE_BANDS[seg]),
            "city": rng.choice(pools.CITIES),
            "income_band": rng.choice(["low", "mid", "mid", "high"] if seg != "first_time" else ["low", "low", "mid"]),
            "active_period": rng.choice(pools.ACTIVE_PERIODS),
            "prompt_set": PROMPT_SET[split],
        })
    return out


def _fix(slot: dict, raw: dict, rng) -> dict:
    """Keep only valid vocabulary, enforce contacts implied by habits, add Python-owned fields."""
    habits = [h for h in raw.get("habits", []) if h in pools.HABITS]
    habits = list(dict.fromkeys(habits))[:7] or ["family_support", "phone_topups"]
    contacts = []
    for c in raw.get("contacts", []):
        if isinstance(c, dict) and c.get("relation") in pools.RELATIONS and isinstance(c.get("name"), str):
            name = c["name"].strip()[:40]
            if name and not any(ch.isdigit() for ch in name):
                contacts.append({"name": name, "relation": c["relation"]})
    for habit, rel in REQUIRED_CONTACT_FOR_HABIT.items():
        if habit in habits and not any(c["relation"] == rel for c in contacts):
            default = {"landlord": "الحاج " + rng.choice(pools.MALE_FIRST), "generator": "مولدة أبو " + rng.choice(pools.MALE_FIRST),
                       "savings_group": "جمعية " + rng.choice(["الدائرة", "الشغل", "الأقارب"])}[rel]
            contacts.append({"name": default, "relation": rel})
    if not any(c["relation"] in {"mother", "father", "son", "daughter", "spouse", "brother", "sister"} for c in contacts):
        contacts.append({"name": rng.choice(pools.FEMALE_FIRST), "relation": "mother"})
    seen, unique = set(), []
    for c in contacts:
        if c["name"] not in seen:
            seen.add(c["name"])
            unique.append(c)
    first = str(raw.get("first_name") or rng.choice(pools.MALE_FIRST if slot["gender"] == "m" else pools.FEMALE_FIRST))
    return {
        **slot,
        "first_name": first.strip()[:30],
        "last_name": str(raw.get("last_name") or rng.choice(pools.LAST_NAMES)).strip()[:30],
        "job": str(raw.get("job") or rng.choice(pools.JOBS[slot["segment"]]))[:60],
        "habits": habits,
        "contacts": unique[:9],
        "tech_comfort": raw.get("tech_comfort") if raw.get("tech_comfort") in pools.TECH_COMFORT else "mid",
        "bio": str(raw.get("bio", ""))[:200],
    }


def offline_persona(slot: dict) -> dict:
    rng = rng_for(SEED, "offline", slot["id"])
    first = rng.choice(pools.MALE_FIRST if slot["gender"] == "m" else pools.FEMALE_FIRST)
    last = rng.choice(pools.LAST_NAMES)
    habits = rng.sample(pools.HABITS, rng.randint(3, 6))
    if slot["segment"] == "retiree":
        habits = list(dict.fromkeys(["family_support", *habits]))
    rels = rng.sample(["mother", "son", "daughter", "brother", "sister", "friend", "colleague", "spouse"], rng.randint(3, 6))
    contacts = [{"name": f"{rng.choice(pools.MALE_FIRST + pools.FEMALE_FIRST)} {rng.choice(pools.LAST_NAMES)}", "relation": r}
                for r in rels]
    raw = {"first_name": first, "last_name": last, "job": rng.choice(pools.JOBS[slot["segment"]]), "habits": habits,
           "contacts": contacts, "tech_comfort": rng.choice(pools.TECH_COMFORT), "bio": ""}
    return {**_fix(slot, raw, rng), "source": "offline"}


def llm_persona(slot: dict) -> dict:
    from data.llm_gen import chat_json

    cfg = PROMPTS[slot["prompt_set"]]
    brief = {k: slot[k] for k in ("segment", "gender", "age_band", "city", "income_band", "active_period")}
    user = (
        f"Slot (fixed, do not change): {json.dumps(brief, ensure_ascii=False)}\n"
        f"Allowed habits: {pools.HABITS}\nAllowed contact relations: {pools.RELATIONS}\n"
        "Return keys: first_name, last_name, job, habits (3-6 from the allowed list), contacts (4-8 items with "
        "name and relation; include a landlord/generator/savings_group contact if you pick rent/generator_fees/"
        "savings_group), tech_comfort (low|mid|high), bio (one Arabic sentence). No numbers anywhere.\n"
        f"Example of the format only: {json.dumps(cfg['example'], ensure_ascii=False)}"
    )
    raw = chat_json(cfg["system"], user, temperature=cfg["temperature"])
    return {**_fix(slot, raw, rng_for(SEED, "llmfix", slot["id"])), "source": "llm", "model_raw": raw}


def generate(splits: list[str], offline: bool, force: bool) -> dict[str, int]:
    use_llm = False
    if not offline:
        from data.llm_gen import reachable

        use_llm = reachable()
        if not use_llm:
            print("LLM unreachable: falling back to offline personas")
    def make(slot: dict) -> dict:
        if use_llm:
            for _attempt in range(3):
                try:
                    return llm_persona(slot)
                except Exception as exc:  # noqa: BLE001 - any LLM failure falls back to offline
                    print(f"  {slot['id']}: LLM attempt failed ({type(exc).__name__}: {exc})")
        return offline_persona(slot)

    counts = {}
    for split in splits:
        out_dir = PERSONA_DIR / split
        out_dir.mkdir(parents=True, exist_ok=True)
        todo = []
        for slot in slots(split):
            path = out_dir / f"{slot['id']}.json"
            if path.exists() and not force:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("source") == "llm" or not use_llm:
                    continue
            todo.append(slot)
        # The server batches concurrent requests; each persona depends only on its own slot.
        with ThreadPoolExecutor(max_workers=8) as pool:
            for slot, persona in zip(todo, pool.map(make, todo), strict=True):
                (out_dir / f"{slot['id']}.json").write_text(json.dumps(persona, ensure_ascii=False, indent=1),
                                                            encoding="utf-8")
                print(f"  {slot['id']} {slot['segment']:<10} {persona['first_name']} {persona['last_name']} "
                      f"[{persona['source']}]")
        counts[split] = len(todo)
    return counts


def load_personas(split: str) -> list[dict]:
    files = sorted((PERSONA_DIR / split).glob("*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=list(SPLITS))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print(generate(a.splits, a.offline, a.force))
