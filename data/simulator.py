"""Seeded simulator: persona (categorical) -> months of IQD wallet history.

Python writes every number: income, rent, bills, top-ups, family transfers, shopping, Eid
bursts, Ramadan hour shifts, honest device events. The same persona and seed always produce
the same history.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from data import pools
from rules.calendar import EID_AL_ADHA, EID_AL_FITR, in_ramadan

SIM_END = datetime(2026, 10, 25, 23, 0)
HISTORY_DAYS = 300
OPERATOR_PREFIXES = ["0770", "0771", "0772", "0773", "0774", "0775", "0780", "0781", "0782", "0790", "0791",
                     "0750", "0751"]
ACTIVE_HOURS = {"morning": (8, 12), "afternoon": (12, 17), "evening": (17, 22), "night": (20, 24)}
# Tuples, not sets: iteration order must not depend on per-process string hashing.
FAMILY_RELATIONS = ("mother", "father", "son", "daughter", "spouse")
SIBLING_FRIEND = ("brother", "sister", "friend", "colleague")
PAYERS = {
    "pension": ("07300000001", "هيئة التقاعد الوطنية"),
    "salary": ("07300000002", "رواتب الدولة - توطين"),
    "cash_in": ("07300000003", "وكيل شحن"),
}


def rng_for(*parts) -> random.Random:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def iqd(x: float) -> int:
    step = 1000 if x >= 100_000 else 250
    return max(step, int(round(x / step)) * step)


class WalletBook:
    """Hands out unique wallet numbers across all generated users."""

    def __init__(self, seed: int | str):
        self.rng = rng_for("wallets", seed)
        self.used: set[str] = {w for w, _, _ in pools.BILLERS} | {w for w, _ in PAYERS.values()}

    def new(self) -> str:
        while True:
            w = self.rng.choice(OPERATOR_PREFIXES) + f"{self.rng.randrange(10**7):07d}"
            if w not in self.used:
                self.used.add(w)
                return w


@dataclass
class SimUser:
    user: dict
    persona: dict
    contacts: list[dict]
    accounts: dict[str, dict]
    history: list[dict]
    device_events: list[dict]
    start_balance: int
    sim_start: datetime
    sim_end: datetime
    params: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        iso = lambda d: d.isoformat() if isinstance(d, datetime) else d  # noqa: E731
        return {
            "user": self.user,
            "persona": self.persona,
            "contacts": [{**c, "saved_at": iso(c["saved_at"])} for c in self.contacts],
            "accounts": [{**a, "created_at": iso(a["created_at"])} for a in self.accounts.values()],
            "history": [{**t, "ts": iso(t["ts"])} for t in self.history],
            "device_events": [{**e, "ts": iso(e["ts"])} for e in self.device_events],
            "start_balance": self.start_balance,
            "sim_start": iso(self.sim_start),
            "sim_end": iso(self.sim_end),
            "params": self.params,
        }


class Simulator:
    def __init__(self, seed: int = 2026, sim_end: datetime = SIM_END, book: WalletBook | None = None):
        self.seed = seed
        self.sim_end = sim_end
        self.book = book or WalletBook(seed)

    @staticmethod
    def hour(rng: random.Random, period: str, day: datetime) -> datetime:
        lo, hi = ACTIVE_HOURS[period]
        if in_ramadan(day) and rng.random() < 0.6:
            hour = rng.choice([19, 20, 21, 22, 23, 0, 1])  # after iftar and late night
        elif rng.random() < 0.8:
            hour = rng.randrange(lo, hi) % 24
        else:
            hour = rng.randrange(7, 24)
        return day.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60))

    @staticmethod
    def account(accounts: dict, wallet: str, name: str, created: datetime, senders: int = 0,
                is_biller: bool = False, is_business: bool = False) -> None:
        accounts.setdefault(wallet, {"wallet": wallet, "display_name": name, "created_at": created,
                                     "reported": False, "is_biller": is_biller, "is_business": is_business,
                                     "unrelated_senders_7d": senders})

    def simulate(self, persona: dict) -> SimUser:
        rng = rng_for(self.seed, persona["id"])
        seg = persona["segment"]
        end = self.sim_end
        days = HISTORY_DAYS if seg != "first_time" else rng.randint(14, 70)
        start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0)
        habits = set(persona.get("habits", []))
        period = persona.get("active_period", "evening")
        band = persona.get("income_band", "mid")
        scale = {"low": 0.7, "mid": 1.0, "high": 1.6}[band]
        hour = lambda d, p=period: self.hour(rng, p, d)  # noqa: E731

        params: dict = {"days": days, "scale": scale, "rent_day": rng.randint(1, 7)}
        if seg == "salaried":
            params["salary"] = iqd(rng.uniform(*{"low": (550_000, 850_000), "mid": (850_000, 1_500_000),
                                                 "high": (1_500_000, 3_000_000)}[band]))
            params["pay_day"] = rng.choice([1, 2, 3, 5, 10, 25, 27])
        elif seg == "retiree":
            params["pension"] = iqd(rng.uniform(450_000, 1_100_000) * (1.2 if band == "high" else 1.0))
            params["pay_day"] = rng.randint(1, 7)
        else:
            params["cash_in_rate"] = rng.uniform(0.06, 0.15)
        start_balance = {"salaried": iqd(rng.uniform(150_000, 1_200_000)),
                         "retiree": iqd(rng.uniform(80_000, 700_000)), "first_time": 0}[seg]

        user = {"id": persona["id"], "wallet": self.book.new(), "first_name": persona["first_name"],
                "last_name": persona.get("last_name", ""), "segment": seg, "city": persona.get("city", "")}
        accounts: dict[str, dict] = {}
        for w, name, _ in pools.BILLERS:
            self.account(accounts, w, name, start - timedelta(days=3000), senders=500, is_biller=True)
        for w, name in PAYERS.values():
            self.account(accounts, w, name, start - timedelta(days=3000))

        contacts = []
        for c in persona.get("contacts", []):
            wallet = self.book.new()
            if seg == "first_time":
                saved = start + timedelta(days=rng.randint(0, 4))
            else:
                saved = start - timedelta(days=rng.randint(30, 700))
            contacts.append({"name": c["name"], "wallet": wallet, "relation": c["relation"], "saved_at": saved})
            self.account(accounts, wallet, c["name"], start - timedelta(days=rng.randint(200, 2500)),
                         senders=rng.randint(0, 2))
        by_rel: dict[str, list[dict]] = {}
        for c in contacts:
            by_rel.setdefault(c["relation"], []).append(c)

        def merchant(pool: list[str], senders: tuple[int, int], age: tuple[int, int], p_business: float) -> tuple[str, str]:
            # Not every shop is a verified business account: small Instagram sellers often use personal wallets.
            w = self.book.new()
            name = rng.choice(pool)
            self.account(accounts, w, name, start - timedelta(days=rng.randint(*age)), senders=rng.randint(*senders),
                         is_business=rng.random() < p_business)
            return w, name

        known_shops = [merchant(pools.ONLINE_SHOPS, (5, 40), (100, 1500), 0.6) for _ in range(rng.randint(1, 3))]
        local_shop = merchant(pools.SHOP_NAMES, (0, 5), (300, 3000), 0.5)
        base_ranges = {"mother": (50_000, 200_000), "father": (50_000, 200_000), "son": (40_000, 150_000),
                       "daughter": (40_000, 150_000), "spouse": (50_000, 250_000)}
        amounts = {rel: iqd(rng.uniform(*base_ranges.get(rel, (20_000, 100_000))) * scale) for rel in pools.RELATIONS}
        rent = iqd(rng.uniform(200_000, 550_000) * scale)
        generator_fee = iqd(rng.uniform(20_000, 60_000))
        savings = iqd(rng.uniform(50_000, 250_000) * scale)
        params.update(rent=rent, generator_fee=generator_fee, savings=savings)

        history: list[dict] = []
        events: list[dict] = []
        balance = start_balance
        eid_days = {d for y in (2025, 2026, 2027) for d in (EID_AL_FITR.get(y), EID_AL_ADHA.get(y)) if d}

        todo: list[tuple] = []  # one day's (ts, direction, wallet, name, amount, kind, note), applied in time order

        def out(ts, w, n, a, kind="p2p", note=""):
            todo.append((ts, "out", w, n, a, kind, note))

        def inc(ts, w, n, a, kind="p2p", note=""):
            todo.append((ts, "in", w, n, a, kind, note))

        day = start
        while day < end:
            todo.clear()

            # Income
            morning = day.replace(hour=rng.randint(7, 11), minute=rng.randrange(60))
            if seg == "salaried" and day.day == params["pay_day"]:
                inc(morning, *PAYERS["salary"], params["salary"], "salary")
            if seg == "retiree" and day.day == params["pay_day"]:
                inc(morning, *PAYERS["pension"], params["pension"], "pension")
            if seg == "first_time" and rng.random() < params["cash_in_rate"]:
                inc(hour(day), *PAYERS["cash_in"], rng.choice([25_000, 50_000, 75_000, 100_000, 150_000, 250_000]),
                    "cash_in")
            for rel in ("son", "daughter", "brother", "sister"):
                for c in by_rel.get(rel, []):
                    if rng.random() < 0.012:
                        inc(hour(day), c["wallet"], c["name"], rng.uniform(25_000, 150_000))

            # Fixed monthly payments
            if "rent" in habits and day.day == params["rent_day"]:
                for c in by_rel.get("landlord", [])[:1]:
                    out(hour(day), c["wallet"], c["name"], rent, note=rng.choice(pools.ROUTINE_NOTES["rent"]))
            if "generator_fees" in habits and day.day == 10:
                for c in by_rel.get("generator", [])[:1]:
                    out(hour(day), c["wallet"], c["name"], generator_fee,
                        note=rng.choice(pools.ROUTINE_NOTES["generator_fees"]))
            if "savings_group" in habits and day.day == 2:
                for c in by_rel.get("savings_group", [])[:1]:
                    out(hour(day), c["wallet"], c["name"], savings, note=rng.choice(pools.ROUTINE_NOTES["savings_group"]))
            if "bills" in habits:
                if day.day == 15:
                    out(hour(day), "07400000001", "الكهرباء - جباية إلكترونية", rng.uniform(15_000, 60_000) * scale, "bill")
                if day.day == 20:
                    out(hour(day), "07400000005", "إيرثلنك - انترنت", rng.choice([25_000, 35_000, 50_000]), "bill")
            if "phone_topups" in habits and rng.random() < 0.1:
                w, n, k = rng.choice(pools.BILLERS[1:4])
                out(hour(day), w, n, rng.choice([5_000, 10_000, 15_000, 25_000]), k)

            # Family and friends
            if "family_support" in habits or seg == "retiree":
                for rel in FAMILY_RELATIONS:
                    for c in by_rel.get(rel, []):
                        if rng.random() < 1.6 / 30:
                            out(hour(day), c["wallet"], c["name"], amounts[rel] * rng.uniform(0.6, 1.3),
                                note=rng.choice(pools.ROUTINE_NOTES["family_support"]))
            for rel in SIBLING_FRIEND:
                for c in by_rel.get(rel, []):
                    if rng.random() < 0.5 / 30:
                        out(hour(day), c["wallet"], c["name"], amounts[rel] * rng.uniform(0.4, 1.5),
                            note=rng.choice(pools.ROUTINE_NOTES["friend"]))

            # Shops, online orders, and occasional honest first-time payees
            if "grocery_shop" in habits and rng.random() < 0.2:
                out(hour(day), *local_shop, rng.uniform(5_000, 40_000), "merchant")
            if "online_shopping" in habits and rng.random() < 0.07:
                if rng.random() < 0.25:
                    known_shops.append(merchant(pools.ONLINE_SHOPS, (5, 40), (30, 900), 0.6))
                    shop = known_shops[-1]
                else:
                    shop = rng.choice(known_shops)
                out(hour(day), *shop, rng.uniform(15_000, 120_000) * scale, "merchant",
                    rng.choice(pools.ROUTINE_NOTES["online_shopping"]))
            if rng.random() < 0.008 or ("medical" in habits and rng.random() < 0.015):
                out(hour(day), *merchant(pools.CLINICS, (3, 20), (200, 3000), 0.85), rng.uniform(25_000, 400_000), "merchant",
                    rng.choice(pools.ROUTINE_NOTES["medical"]))
            if "school_fees" in habits and (day.month, day.day) in {(9, 28), (2, 3)} and rng.random() < 0.8:
                out(hour(day), *merchant(pools.SCHOOLS, (5, 30), (300, 3000), 0.9), rng.uniform(250_000, 1_200_000) * scale,
                    "merchant", rng.choice(pools.ROUTINE_NOTES["school_fees"]))
            if "religious_donations" in habits and rng.random() < (0.08 if in_ramadan(day) else 0.01):
                out(hour(day), *merchant(pools.CHARITIES, (10, 100), (500, 3000), 0.9), rng.uniform(5_000, 50_000), "p2p",
                    rng.choice(pools.ROUTINE_NOTES["religious_donations"]))

            # Eid: a burst of small gifts within minutes, sometimes to relatives who are not saved
            if day.date() in eid_days and seg != "first_time":
                ts = self.hour(rng, "morning", day)
                recipients = [c for c in contacts if c["relation"] in FAMILY_RELATIONS + SIBLING_FRIEND]
                rng.shuffle(recipients)
                for c in recipients[: rng.randint(1, 4)]:
                    ts += timedelta(minutes=rng.randint(1, 6))
                    out(ts, c["wallet"], c["name"], rng.choice([10_000, 25_000, 50_000, 100_000]) * scale,
                        note=rng.choice(pools.ROUTINE_NOTES["eid"]))
                for _ in range(rng.choice([0, 0, 1, 2])):
                    ts += timedelta(minutes=rng.randint(1, 6))
                    w = self.book.new()
                    name = rng.choice(pools.MALE_FIRST + pools.FEMALE_FIRST) + " " + user["last_name"]
                    self.account(accounts, w, name, start - timedelta(days=rng.randint(100, 2000)))
                    out(ts, w, name, rng.choice([10_000, 25_000, 50_000]) * scale,
                        note=rng.choice(pools.ROUTINE_NOTES["eid"]))

            # Honest security events
            for kind, per_year in (("new_device", 0.8), ("pin_reset", 1.0), ("sim_change", 0.5)):
                if rng.random() < per_year / 365:
                    events.append({"ts": hour(day), "kind": kind})

            for ts, direction, w, n, a, kind, note in sorted(todo, key=lambda x: x[0]):
                if ts >= end:
                    continue
                a = iqd(a)
                if direction == "out":
                    if a > balance - 1000:
                        continue  # cannot afford it today; skip rather than overdraw
                    balance -= a
                else:
                    balance += a
                history.append({"ts": ts, "direction": direction, "counterparty": w, "counterparty_name": n,
                                "amount": a, "note": note, "kind": kind})
            day += timedelta(days=1)

        history.sort(key=lambda t: t["ts"])
        return SimUser(user=user, persona=persona, contacts=contacts, accounts=accounts, history=history,
                       device_events=sorted(events, key=lambda e: e["ts"]), start_balance=start_balance,
                       sim_start=start, sim_end=end, params=params)
