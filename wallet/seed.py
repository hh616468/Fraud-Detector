"""Seed the demo wallet.

    python -m wallet.seed                 # three hand-built demo personas
    python -m wallet.seed --generated dev # plus the generated dev-split users, shifted to end now

Histories are deterministic (fixed seeds). Demo users share the PIN in DEMO_PIN (default 1234),
a test value for this mock wallet only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, insert, select

from config import ROOT, settings
from wallet import db
from wallet.service import hash_pin, now_baghdad

DEMO_PIN = os.getenv("DEMO_PIN", "1234")

BILLERS = [
    ("07400000001", "الكهرباء - جباية إلكترونية"),
    ("07400000002", "زين للاتصالات - شحن رصيد"),
    ("07400000003", "إيرثلنك - انترنت"),
]

DEMO_USERS = [
    {
        "id": "demo_retiree", "first_name": "أم علي", "last_name": "الجبوري", "segment": "retiree", "city": "بغداد",
        "wallet": "07701000001", "pension": 650_000, "pay_day": 3,
        "contacts": [("علي حسن", "07702000001", "son", 120_000), ("زينب حسن", "07802000002", "daughter", 75_000),
                     ("أبو كرار المولدة", "07502000003", "generator", 35_000)],
        "hours": (9, 13),
    },
    {
        "id": "demo_salaried", "first_name": "مصطفى", "last_name": "كاظم", "segment": "salaried", "city": "البصرة",
        "wallet": "07801000002", "salary": 1_250_000, "pay_day": 1,
        "contacts": [("حسين علي", "07703000001", "brother", 150_000), ("الحاج عباس - إيجار", "07803000002", "landlord", 400_000),
                     ("جمعية الدائرة", "07503000003", "savings_group", 100_000), ("مريم كاظم", "07903000004", "sister", 60_000)],
        "hours": (17, 22),
    },
    {
        "id": "demo_first_time", "first_name": "زهراء", "last_name": "عبد الله", "segment": "first_time", "city": "النجف",
        "wallet": "07901000003", "cash_in": 150_000,
        "contacts": [("أمي", "07704000001", "mother", 25_000)],
        "hours": (15, 21),
        "history_days": 20,
    },
]


def _ts(rng: random.Random, day: datetime, hours: tuple[int, int]) -> datetime:
    return day.replace(hour=rng.randint(*hours), minute=rng.randint(0, 59), second=0, microsecond=0)


def demo_history(u: dict, end: datetime, seed: int) -> list[dict]:
    rng = random.Random(seed)
    days = u.get("history_days", 180)
    start = end - timedelta(days=days)
    rows: list[dict] = []

    def add(ts, direction, wallet, name, amount, kind, note=""):
        rows.append(dict(ts=ts, direction=direction, counterparty=wallet, counterparty_name=name,
                         amount=int(round(amount / 250) * 250), kind=kind, note=note))

    day = start
    while day < end - timedelta(hours=6):
        if "pension" in u and day.day == u["pay_day"]:
            add(_ts(rng, day, (8, 10)), "in", "07300000001", "هيئة التقاعد الوطنية", u["pension"], "pension")
        if "salary" in u and day.day == u["pay_day"]:
            add(_ts(rng, day, (8, 10)), "in", "07300000002", "راتب - وزارة النفط", u["salary"], "salary")
        if "cash_in" in u and rng.random() < 0.12:
            add(_ts(rng, day, u["hours"]), "in", "07300000003", "وكيل شحن", u["cash_in"], "cash_in")
        for name, wallet, rel, base in u["contacts"]:
            p = {"son": 0.05, "daughter": 0.05, "brother": 0.06, "sister": 0.05, "mother": 0.08,
                 "generator": 1 / 30, "landlord": 1 / 30, "savings_group": 1 / 30}[rel]
            if rel in ("landlord", "savings_group", "generator"):
                due = day.day == {"landlord": 5, "savings_group": 2, "generator": 10}[rel]
                if due:
                    note = {"landlord": "ايجار الشهر", "savings_group": "قسط الجمعية", "generator": "اجور المولدة"}[rel]
                    add(_ts(rng, day, u["hours"]), "out", wallet, name, base, "p2p", note)
            elif rng.random() < p:
                add(_ts(rng, day, u["hours"]), "out", wallet, name, base * rng.uniform(0.6, 1.4), "p2p")
        if rng.random() < 0.08:
            wallet, name = BILLERS[rng.randrange(len(BILLERS))]
            add(_ts(rng, day, u["hours"]), "out", wallet, name, rng.choice([10_000, 15_000, 25_000, 35_000]), "bill")
        day += timedelta(days=1)
    rows.sort(key=lambda r: r["ts"])
    return rows


def _insert_user(conn, u: dict, history: list[dict], start_balance: int, split: str, persona=None,
                 contacts_saved_at: datetime | None = None, extra_accounts: list[dict] | None = None) -> None:
    def ensure(wallet, name, created, is_biller=False, reported=False, senders=0):
        if conn.execute(select(db.accounts.c.wallet).where(db.accounts.c.wallet == wallet)).first() is None:
            conn.execute(insert(db.accounts).values(wallet=wallet, display_name=name, created_at=created,
                                                    is_biller=is_biller, reported=reported,
                                                    unrelated_senders_7d=senders))

    first_ts = history[0]["ts"] if history else now_baghdad()
    ensure(u["wallet"], f"{u['first_name']} {u['last_name']}".strip(), first_ts - timedelta(days=30))
    for w, name in BILLERS:
        ensure(w, name, first_ts - timedelta(days=2000), is_biller=True)
    for acc in extra_accounts or []:
        ensure(acc["wallet"], acc.get("display_name", ""), acc.get("created_at"), acc.get("is_biller", False),
               acc.get("reported", False), acc.get("unrelated_senders_7d", 0))

    balance = start_balance + sum(r["amount"] if r["direction"] == "in" else -r["amount"] for r in history)
    conn.execute(insert(db.users).values(
        id=u["id"], wallet=u["wallet"], first_name=u["first_name"], last_name=u.get("last_name", ""),
        segment=u.get("segment", ""), city=u.get("city", ""), split=split, balance=balance,
        pin_hash=hash_pin(DEMO_PIN), persona=persona,
    ))
    saved = contacts_saved_at or (first_ts - timedelta(days=60))
    for c in u["contacts"]:
        name, wallet = c[0], c[1]
        ensure(wallet, name, first_ts - timedelta(days=400))
        conn.execute(insert(db.contacts).values(user_id=u["id"], name=name, wallet=wallet, saved_at=saved))
    for r in history:
        ensure(r["counterparty"], r["counterparty_name"], first_ts - timedelta(days=300))
        conn.execute(insert(db.transactions).values(
            user_id=u["id"], direction=r["direction"], counterparty=r["counterparty"],
            counterparty_name=r["counterparty_name"], amount=r["amount"], note=r.get("note", ""), kind=r["kind"],
            status="committed", draft_version=1, created_at=r["ts"], updated_at=r["ts"], committed_at=r["ts"],
        ))


def reset(engine, user_ids: list[str]) -> None:
    with engine.begin() as conn:
        for uid in user_ids:
            tx_ids = [r[0] for r in conn.execute(select(db.transactions.c.id).where(db.transactions.c.user_id == uid))]
            a_ids = [r[0] for r in conn.execute(select(db.assessments.c.id).where(db.assessments.c.transfer_id.in_(tx_ids)))]
            conn.execute(delete(db.coach_turns).where(db.coach_turns.c.assessment_id.in_(a_ids)))
            conn.execute(delete(db.decisions).where(db.decisions.c.transfer_id.in_(tx_ids)))
            conn.execute(delete(db.assessments).where(db.assessments.c.id.in_(a_ids)))
            conn.execute(delete(db.transactions).where(db.transactions.c.user_id == uid))
            conn.execute(delete(db.contacts).where(db.contacts.c.user_id == uid))
            conn.execute(delete(db.device_events).where(db.device_events.c.user_id == uid))
            conn.execute(delete(db.users).where(db.users.c.id == uid))


def seed_demo(engine, end: datetime | None = None) -> list[str]:
    end = end or now_baghdad()
    reset(engine, [u["id"] for u in DEMO_USERS])
    with engine.begin() as conn:
        for i, u in enumerate(DEMO_USERS):
            start_balance = {"retiree": 400_000, "salaried": 900_000, "first_time": 50_000}[u["segment"]]
            _insert_user(conn, u, demo_history(u, end, seed=100 + i), start_balance, "demo")
    return [u["id"] for u in DEMO_USERS]


def seed_generated(engine, split: str, end: datetime | None = None) -> list[str]:
    """Load generated users of one split, shifting timestamps so their history ends at `end`."""
    end = end or now_baghdad()
    users_dir = ROOT / "data" / "generated" / split / "users"
    if not users_dir.exists():
        raise SystemExit(f"no generated users in {users_dir}; run `python -m data.build` first")
    files = sorted(users_dir.glob("*.json"))
    loaded = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    reset(engine, [u["user"]["id"] for u in loaded])
    with engine.begin() as conn:
        for u in loaded:
            sim_end = datetime.fromisoformat(u["sim_end"])
            shift = end - sim_end
            mv = lambda iso, shift=shift: datetime.fromisoformat(iso) + shift if iso else None  # noqa: E731
            history = [{**r, "ts": mv(r["ts"])} for r in u["history"]]
            user = {**u["user"], "contacts": [(c["name"], c["wallet"]) for c in u["contacts"]]}
            accounts = [{**a, "created_at": mv(a.get("created_at"))} for a in u.get("accounts", [])]
            _insert_user(conn, user, history, u["start_balance"], split, persona=u.get("persona"),
                         contacts_saved_at=mv(u["contacts"][0]["saved_at"]) if u["contacts"] else None,
                         extra_accounts=accounts)
            for e in u.get("device_events", []):
                conn.execute(insert(db.device_events).values(user_id=user["id"], kind=e["kind"], ts=mv(e["ts"])))
    return [u["user"]["id"] for u in loaded]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generated", choices=["dev", "val"], help="also load generated users of this split")
    ap.add_argument("--db", default=settings.db_path)
    args = ap.parse_args()
    engine = db.make_engine(args.db)
    ids = seed_demo(engine)
    if args.generated:
        ids += seed_generated(engine, args.generated)
    print(f"seeded {len(ids)} users into {Path(args.db)}")


if __name__ == "__main__":
    main()
