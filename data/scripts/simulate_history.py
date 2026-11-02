"""Turn personas into wallet histories: accounts, saved contacts, transactions and device events.

Python writes every number here; the LLM only wrote the personas. Each user gets their own
seeded random stream, so a run is reproducible and adding users does not change existing ones.
No scams are generated here: every transaction in this output is legitimate. The scam injector
adds scams on top later.

Usage:
    python data/scripts/simulate_history.py --split devval

Reads data/<split>/personas.jsonl and writes data/<split>/history/.
"""

import argparse
import csv
import hashlib
import json
import math
import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parents[1]
BAGHDAD = timezone(timedelta(hours=3))

START, END = date(2025, 12, 1), date(2026, 6, 15)
# Approximate dates for Iraq; the official start can differ by a day.
RAMADAN = (date(2026, 2, 18), date(2026, 3, 19))
EID_FITR, EID_ADHA = date(2026, 3, 20), date(2026, 5, 27)

# Behaviour rates. These are our assumptions, not measured statistics; the README lists them.
A = {
    "wallet_share": (0.4, 1.0),          # share of monthly income that passes through the wallet
    "off_hours_rate": 0.08,              # user-initiated transfers outside active hours
    "ramadan_night_shift": 0.6,          # chance a Ramadan transfer moves 4 h later
    "call_active_p2p": 0.03,             # honest transfer made while on a phone call
    "call_active_other": 0.01,
    "new_payee_per_month": 0.8,          # honest first transfers to accounts never paid before
    "young_new_payee": 0.25,             # share of those accounts opened under 7 days earlier
    "deposit_note": 0.05,                # honest "عربون" on a purchase from a new payee
    "reason_tap_skip": 0.4,              # new-payee reason question left unanswered
    "contact_saved_in_window": 0.15,     # contacts saved during the simulated period
    "eid_gift": 0.6,                     # each family contact gets an Eid gift
    "adha_sacrifice": 0.3,               # users above 500k/month buying a sheep before Eid al-Adha
    "mistaken_credit": 0.1,              # users who get money by mistake from a stranger and refund it
    "topup_when_short": 0.7,             # short of balance: cash in at an agent first (else give up)
    "device_per_year": {"new_device": 0.8, "sim_change": 0.3},
    "pin_reset_per_year": {"low": 1.5, "medium": 0.6, "high": 0.3},
}

AMOUNT = {"5k-25k": (5_000, 25_000), "25k-100k": (25_000, 100_000), "100k-250k": (100_000, 250_000),
          "250k-500k": (250_000, 500_000), "500k-1M": (500_000, 1_000_000), ">1M": (1_000_000, 3_000_000)}
INCOME = {"<300k": (150_000, 300_000), "300k-500k": (300_000, 500_000), "500k-750k": (500_000, 750_000),
          "750k-1M": (750_000, 1_000_000), "1M-1.5M": (1_000_000, 1_500_000),
          "1.5M-2.5M": (1_500_000, 2_500_000), ">2.5M": (2_500_000, 4_000_000)}
BILL_AMOUNT = {"generator": (25_000, 100_000), "internet": (25_000, 50_000), "water_electricity": (10_000, 50_000),
               "school_fees": (100_000, 250_000), "rent": (250_000, 500_000), "installment": (50_000, 200_000)}
TOPUPS = [5_000, 10_000, 10_000, 25_000]

FAMILY = {"parent", "child", "sibling", "spouse", "relative"}
PERSON = FAMILY | {"friend", "colleague", "landlord", "employer", "customer"}
NOTES = {
    "child": ["مصروف", "مصروفك", "للجامعة", "خرجية"], "parent": ["لماما", "مصرف البيت", "للدوا"],
    "spouse": ["مصرف البيت", "للسوق", "اغراض البيت"], "sibling": ["دين", "مثل ما اتفقنا", "للبيت"],
    "relative": ["امانة", "مساعدة", "حسابك"], "friend": ["دين", "رجعت الدين", "حصتك"],
    "shop": ["حساب", "دين", "باقي الحساب"], "service": ["اشتراك", "حساب", "اجور"],
    "landlord": ["ايجار", "ايجار الشهر"], "employer": ["اجور", "راتب"], "customer": ["اجور", "شغل"],
    "colleague": ["حصتك", "دين"],
}
KIN_WORDS = {"ابني", "ابنتي", "بنتي", "اختي", "اخوي", "اخي", "امي", "ابوي", "بابا", "ماما", "والدي", "والدتي",
             "زوجي", "زوجتي", "صديقي", "صديقتي", "صاحبي", "خالتي", "عمتي", "خالي", "عمي", "جدي", "جدتي",
             "حفيدي", "احفادي", "ابن", "ابنة", "بنت", "زوج", "مرت", "الحفيد", "الحفيدة"}
FIRST_NAMES = ["علي", "حسين", "محمد", "احمد", "مصطفى", "حيدر", "عباس", "كرار", "زينب", "فاطمة", "مريم",
               "نور", "سارة", "هدى", "رقية", "عمر", "يوسف", "سجاد", "زهراء", "ايات"]
SURNAMES = ["الربيعي", "الجبوري", "التميمي", "الساعدي", "العبيدي", "الخفاجي", "الزبيدي", "الدليمي", "الشمري",
            "الكعبي", "الموسوي", "الحسيني", "العزاوي", "الجنابي", "اللامي", "البياتي", "الكبيسي", "العامري"]
KURDISH_SURNAMES = ["عزيز", "رشيد", "قادر", "حمه امين", "عبدالله", "كريم", "محمود", "صالح"]
KURDISH_CITIES = {"Erbil", "Sulaymaniyah", "Duhok"}
TELECOMS = {"078": "زين العراق - شحن", "077": "اسياسيل - شحن", "075": "كورك - شحن"}
ISPS = ["شبكة النور للانترنت", "سما نت", "الافق للاتصالات"]


def round_iqd(x):
    step = 250 if x < 10_000 else 1_000 if x < 100_000 else 5_000 if x < 1_000_000 else 25_000
    return max(step, round(x / step) * step)


def draw(rng, lo_hi, scale=1.0, sigma=0.3):
    """Lognormal around the band's geometric middle, kept near the band."""
    lo, hi = lo_hi
    x = math.exp(rng.gauss(math.log(math.sqrt(lo * hi)), sigma)) * scale
    return round_iqd(min(max(x, lo * 0.8 * scale), hi * 1.2 * scale))


def month_starts(first, last):
    d = first.replace(day=1)
    while d <= last:
        yield d
        d = (d + timedelta(days=32)).replace(day=1)


def schedule(rng, freq, first, last):
    days = []
    if freq == "weekly":
        d = first + timedelta(days=rng.randint(0, 6))
        while d <= last:
            if rng.random() > 0.15:
                days.append(d + timedelta(days=rng.randint(-1, 1)))
            d += timedelta(days=7)
    elif freq == "monthly":
        dom = rng.randint(1, 28)
        for m in month_starts(first, last):
            if rng.random() > 0.1:
                days.append(m.replace(day=dom) + timedelta(days=rng.randint(-2, 2)))
    else:
        rate = {"occasional": 1 / 40, "rare": 1 / 120}[freq]
        d = first
        while (d := d + timedelta(days=max(1, round(rng.expovariate(rate))))) <= last:
            days.append(d)
    return [d for d in days if first <= d <= last]


def in_ramadan(day):
    return RAMADAN[0] <= day <= RAMADAN[1]


def at(day, hour, rng):
    return datetime(day.year, day.month, day.day, hour, rng.randint(0, 59), rng.randint(0, 59), tzinfo=BAGHDAD)


def user_time(rng, day, persona):
    """When this user starts a transfer: mostly in their active hours, later at night in Ramadan."""
    h0, h1 = persona["active_hours"]["start"], persona["active_hours"]["end"]
    if rng.random() < A["off_hours_rate"]:
        return at(day, rng.randint(0, 23), rng)
    hour = (h0 + rng.randrange((h1 - h0) % 24 or 24)) % 24
    if in_ramadan(day) and rng.random() < A["ramadan_night_shift"]:
        hour = (hour + 4) % 24
    return at(day, hour, rng)


def other_time(rng, day):
    """When someone else's transfer reaches this user."""
    return at(day, rng.randint(8, 23), rng)


def note(rng, relation, empty=0.55):
    return "" if rng.random() < empty else rng.choice(NOTES.get(relation, [""]))


class World:
    """All wallet accounts, shared across users: billers, agents and every person or shop."""

    def __init__(self, seed, tag):
        self.rng = random.Random(f"{seed}:world")
        self.tag = tag  # split letter, so dev and test account IDs never collide
        self.recipients, self.phones, self.shared = [], set(), {}

    def phone(self, rng, prefix=None):
        while True:
            p = (prefix or rng.choice(["078", "078", "077", "075"])) + str(rng.randint(0, 9)) + \
                f"{rng.randint(0, 9_999_999):07d}"
            if p not in self.phones:
                self.phones.add(p)
                return p

    def add(self, rng, name, kind, city, created, is_biller=False):
        rid = f"R{self.tag}{len(self.recipients) + 1:05d}"
        self.recipients.append({"recipient_id": rid, "display_name": name, "phone": self.phone(rng),
                                "kind": kind, "city": city, "created_at": created.isoformat(),
                                "is_biller": is_biller, "reported": False})
        return rid

    def get_shared(self, key, name, kind, city="", is_biller=False):
        """One account many users pay, e.g. the agent in a city or a telecom."""
        if key not in self.shared:
            created = START - timedelta(days=self.rng.randint(800, 3000))
            self.shared[key] = self.add(self.rng, name, kind, city, created, is_biller)
        return self.shared[key]


def registered_name(rng, saved_name, relation, user_surname, city):
    """The name on the contact's wallet account, which differs from how the user saved them."""
    if relation not in PERSON:
        return saved_name
    words = [w for w in re.sub("[أإآ]", "ا", saved_name).split() if w not in KIN_WORDS]
    first = " ".join(words) if words else rng.choice(FIRST_NAMES)
    if relation in {"child", "sibling", "parent"} and user_surname:
        surname = user_surname
    else:
        surname = rng.choice(KURDISH_SURNAMES if city in KURDISH_CITIES else SURNAMES)
    return f"{first} {surname}"


def simulate_user(p, world, seed):
    rng = random.Random(f"{seed}:{p['user_id']}")
    city = p["city"]
    if p["segment"] == "first_time":
        created = END - timedelta(days=rng.randint(30, 100))
    else:
        created = START - timedelta(days=rng.randint(200, 1500))
    first = max(START, created)
    name_parts = p["name"].split()
    surname = name_parts[-1] if len(name_parts) > 1 and not p["name"].startswith(("أم", "ام", "أبو", "ابو")) else ""

    user = {"user_id": p["user_id"], "phone": world.phone(rng, "078"), "city": city,
            "wallet_created_at": created.isoformat(), "segment": p["segment"]}
    events, contacts, devices = [], [], []

    def ev(ts, direction, kind, cp, amount, note_text="", by_user=None, new_payee=False):
        if ts.date() >= first:
            events.append({"ts": ts, "direction": direction, "type": kind, "counterparty_id": cp,
                           "amount_iqd": amount, "note": note_text,
                           "by_user": direction == "out" if by_user is None else by_user,
                           "new_payee": new_payee})

    # Saved contacts become wallet accounts.
    contact_ids = []
    for c in p["contacts"]:
        kind = "person" if c["relation"] in PERSON else "merchant"
        rid = world.add(rng, registered_name(rng, c["name"], c["relation"], surname, city), kind, city,
                        START - timedelta(days=rng.randint(60, 2500)))
        if p["segment"] == "first_time":
            saved = created + timedelta(days=rng.randint(0, 20))
        elif rng.random() < A["contact_saved_in_window"]:
            saved = START + timedelta(days=rng.randint(0, (END - START).days))
        else:
            saved = START - timedelta(days=rng.randint(31, 1200))
        contacts.append({"user_id": p["user_id"], "recipient_id": rid, "saved_name": c["name"],
                         "saved_at": saved.isoformat()})
        contact_ids.append(rid)

    # Income.
    monthly = draw(rng, INCOME[p["income_band"]]) * rng.uniform(*A["wallet_share"])
    src = p["income_source"]
    payer = next((rid for c, rid in zip(p["contacts"], contact_ids)
                  if c["relation"] == "employer" and c["direction"] != "send"), None)
    paid_by_contact = set()
    if src in ("gov_salary", "private_salary", "pension"):
        if src == "pension":
            payer = world.get_shared("pension", "هيئة التقاعد الوطنية - رواتب", "employer")
        elif src == "gov_salary" and payer is None:
            payer = world.get_shared("state_payroll", "رواتب موظفي الدولة", "employer")
        elif payer is None:
            payer = world.add(rng, f"شركة {rng.choice(SURNAMES)[2:]} للتجارة", "employer", city,
                              START - timedelta(days=rng.randint(400, 3000)))
        paid_by_contact.add(payer)
        amount = round_iqd(monthly)
        for m in month_starts(first, END):
            day = m.replace(day=p["income_day"]) + timedelta(days=rng.choice([0, 0, 0, -1, 1]))
            ev(at(day, rng.randint(7, 12), rng), "in", "pension" if src == "pension" else "salary", payer, amount)
    elif src in ("daily_work", "freelance"):
        payers = [rid for c, rid in zip(p["contacts"], contact_ids)
                  if c["relation"] in ("employer", "customer") and c["direction"] != "send"]
        paid_by_contact.update(payers)
        per_week = rng.randint(2, 4) if src == "daily_work" else rng.uniform(0.5, 1.2)
        day = first
        while (day := day + timedelta(days=max(1, round(rng.expovariate(per_week / 7))))) <= END:
            amt = round_iqd(monthly / (per_week * 4.3) * rng.uniform(0.6, 1.4))
            if payers:
                ev(other_time(rng, day), "in", "p2p", rng.choice(payers), amt, note(rng, "customer"))
            else:
                agent = world.get_shared(f"agent:{city}", f"وكيل زين كاش - {city}", "agent", city)
                ev(user_time(rng, day, p), "in", "cash_in", agent, amt, by_user=True)
    else:  # family_support / none: money arrives through receive contacts or cash at an agent
        agent = world.get_shared(f"agent:{city}", f"وكيل زين كاش - {city}", "agent", city)
        for day in schedule(rng, "monthly", first, END):
            ev(user_time(rng, day, p), "in", "cash_in", agent, round_iqd(monthly * rng.uniform(0.3, 0.8)),
               by_user=True)

    # Regular transfers with contacts.
    for c, rid in zip(p["contacts"], contact_ids):
        if rid in paid_by_contact:
            continue
        lo_hi = AMOUNT[c["amount_band"]]
        typ = "p2p" if c["relation"] in PERSON else "merchant"
        for day in schedule(rng, c["frequency"], first, END):
            incoming = c["direction"] == "receive" or (c["direction"] == "both" and rng.random() < 0.5)
            if incoming:
                ev(other_time(rng, day), "in", typ, rid, draw(rng, lo_hi), note(rng, c["relation"]))
            else:
                ev(user_time(rng, day, p), "out", typ, rid, draw(rng, lo_hi), note(rng, c["relation"]))

    # Bills. Skip ones a saved contact already covers (e.g. a generator saved as a contact).
    names = " ".join(c["name"] for c in p["contacts"])
    for b in p["bills"]:
        kind = b["kind"]
        if kind == "generator" and "مولدة" in names:
            continue
        if kind == "rent" and any(c["relation"] == "landlord" for c in p["contacts"]):
            continue
        if kind == "phone_topup":
            cp = world.get_shared(f"telecom:{user['phone'][:3]}", TELECOMS[user["phone"][:3]], "biller",
                                  is_biller=True)
            for day in schedule(rng, b["frequency"], first, END):
                ev(user_time(rng, day, p), "out", "topup", cp, rng.choice(TOPUPS))
            continue
        cp = {
            "generator": lambda: world.get_shared(f"gen:{city}", f"مولدة الحي - {city}", "biller", city, True),
            "internet": lambda isp=rng.choice(ISPS): world.get_shared(f"isp:{isp}", isp, "biller", "", True),
            "water_electricity": lambda: world.get_shared("electricity", "دائرة الكهرباء - الجباية", "biller",
                                                          "", True),
        }.get(kind, lambda: world.add(rng, {"school_fees": "مدرسة اهلية", "rent": "صاحب الملك",
                                            "installment": "معرض التقسيط"}[kind], "biller", city,
                                      START - timedelta(days=rng.randint(200, 2000)), True))()
        base = draw(rng, BILL_AMOUNT[kind])
        jitter = 0.05 if kind in ("generator", "water_electricity") else 0
        for day in schedule(rng, b["frequency"], first, END):
            text = "رسوم المدرسة" if kind == "school_fees" else ""   # an honest use of a R10 word
            ev(user_time(rng, day, p), "out", "bill", cp, round_iqd(base * rng.uniform(1 - jitter, 1 + jitter)),
               text)

    # Honest first transfers to accounts never paid before: online sellers, a new shop, a repairman.
    def new_payee(day, name, kind, lo_hi, text=None, scale=1.0):
        young = rng.random() < A["young_new_payee"]
        opened = day - timedelta(days=rng.randint(0, 6) if young else rng.randint(30, 1500))
        rid = world.add(rng, name, kind, city, opened)
        if text is None:
            text = "عربون" if rng.random() < A["deposit_note"] else rng.choice(["", "", "حساب", "سعر الغرض"])
        ev(user_time(rng, day, p), "out", "p2p" if kind == "person" else "merchant", rid,
           draw(rng, lo_hi, scale), text, new_payee=True)
        if rng.random() < 0.3:
            contacts.append({"user_id": p["user_id"], "recipient_id": rid,
                             "saved_name": name.split()[0], "saved_at": day.isoformat()})

    months = (END - first).days / 30
    for _ in range(sum(rng.random() < A["new_payee_per_month"] / 4 for _ in range(round(months * 4)))):
        day = first + timedelta(days=rng.randint(0, (END - first).days))
        if rng.random() < 0.5:
            new_payee(day, f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}", "person",
                      AMOUNT[p["typical_transfer_band"]], scale=rng.uniform(0.5, 2))
        else:
            new_payee(day, rng.choice(["متجر اونلاين", "محل موبايلات", "فني تبريد", "محل اثاث", "صيدلية"]) +
                      f" {rng.choice(SURNAMES)[2:]}", "merchant", AMOUNT[p["typical_transfer_band"]],
                      scale=rng.uniform(0.5, 2))

    # Eid: gifts to family, zakat al-fitr, and a sheep before Eid al-Adha.
    for eid in (EID_FITR, EID_ADHA):
        for c, rid in zip(p["contacts"], contact_ids):
            if c["relation"] in FAMILY and c["direction"] != "receive" and rng.random() < A["eid_gift"]:
                day = eid - timedelta(days=rng.randint(0, 4))
                ev(user_time(rng, day, p), "out", "p2p", rid, draw(rng, AMOUNT[c["amount_band"]],
                   rng.uniform(1.5, 3)), rng.choice(["عيدية", "عيدكم مبارك", ""]))
    charity = world.get_shared(f"charity:{city}", f"جامع الحي - {city} - زكاة", "merchant", city)
    ev(user_time(rng, EID_FITR - timedelta(days=rng.randint(1, 3)), p), "out", "merchant", charity,
       draw(rng, (15_000, 60_000)), "زكاة الفطر")
    if INCOME[p["income_band"]][0] >= 500_000 and rng.random() < A["adha_sacrifice"]:
        new_payee(EID_ADHA - timedelta(days=rng.randint(2, 8)), f"بائع اغنام {rng.choice(SURNAMES)[2:]}",
                  "person", (250_000, 600_000), text=rng.choice(["عربون", "سعر الخروف", ""]))

    # Money sent by mistake from a stranger, refunded by the user (honest version of R11).
    if rng.random() < A["mistaken_credit"]:
        day = first + timedelta(days=rng.randint(0, (END - first).days))
        stranger = world.add(rng, f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}", "person", city,
                             START - timedelta(days=rng.randint(60, 2000)))
        amt = draw(rng, (25_000, 250_000))
        t = other_time(rng, day)
        ev(t, "in", "p2p", stranger, amt)
        ev(t + timedelta(hours=rng.uniform(0.3, 48)), "out", "refund", stranger, amt, "رجعت فلوسك")

    # Device and security events.
    dev_n = 1
    devices.append({"user_id": p["user_id"], "ts": at(created, rng.randint(9, 20), rng).isoformat(),
                    "event": "account_created", "device_id": f"{p['user_id']}-d1"})
    rates = dict(A["device_per_year"], pin_reset=A["pin_reset_per_year"][p["phone_literacy"]])
    for kind in sorted(rates):
        day = first
        while (day := day + timedelta(days=max(1, round(rng.expovariate(rates[kind] / 365))))) <= END:
            if kind == "new_device":
                dev_n += 1
            devices.append({"user_id": p["user_id"], "ts": user_time(rng, day, p).isoformat(), "event": kind,
                            "device_id": f"{p['user_id']}-d{dev_n}"})
    devices.sort(key=lambda d: d["ts"])
    dev_n = 1
    for d in devices:  # renumber in time order
        if d["event"] == "new_device":
            dev_n += 1
        d["device_id"] = f"{p['user_id']}-d{dev_n}"

    # Walk events in time order: keep the balance at or above zero, fill in device and call flags.
    agent = world.get_shared(f"agent:{city}", f"وكيل زين كاش - {city}", "agent", city)
    balance = 0 if created >= START else round_iqd(monthly * rng.uniform(0.1, 0.5))
    user["opening_balance_iqd"] = balance
    paid_before, rows, di = set(), [], 0
    for e in sorted(events, key=lambda e: e["ts"]):
        while di < len(devices) and devices[di]["ts"] <= e["ts"].isoformat():
            di += 1
        device = devices[di - 1]["device_id"] if di else f"{p['user_id']}-d1"
        if e["direction"] == "out" and e["amount_iqd"] > balance:
            if rng.random() > A["topup_when_short"]:
                continue
            need = round_iqd(e["amount_iqd"] - balance + rng.uniform(0, 50_000))
            need = math.ceil(need / 25_000) * 25_000
            balance += need
            rows.append(dict(ts=e["ts"] - timedelta(minutes=rng.randint(10, 90)), direction="in", type="cash_in",
                             counterparty_id=agent, amount_iqd=need, balance_after=balance, note="",
                             device_id=device, call_active=False, reason_tap=""))
        balance += e["amount_iqd"] if e["direction"] == "in" else -e["amount_iqd"]
        reason = ""
        if e["direction"] == "out" and e["counterparty_id"] not in paid_before and e["new_payee"]:
            if rng.random() > A["reason_tap_skip"]:
                reason = rng.choice(["family_help", "purchase", "bill_or_rent", "pay_back_debt", "other"])
        call = e["direction"] == "out" and e["by_user"] and \
            rng.random() < (A["call_active_p2p"] if e["type"] == "p2p" else A["call_active_other"])
        if e["direction"] == "out":
            paid_before.add(e["counterparty_id"])
        rows.append(dict(ts=e["ts"], direction=e["direction"], type=e["type"], counterparty_id=e["counterparty_id"],
                         amount_iqd=e["amount_iqd"], balance_after=balance, note=e["note"],
                         device_id=device if e["by_user"] else "", call_active=call, reason_tap=reason))
    for r in rows:
        r["user_id"] = p["user_id"]
        r["ts"] = r["ts"].isoformat()
    return user, contacts, rows, devices


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["devval", "test"])
    ap.add_argument("--seed", type=int, default=20261030)
    args = ap.parse_args()

    src = DATA / args.split / "personas.jsonl"
    out = DATA / args.split / "history"
    out.mkdir(parents=True, exist_ok=True)
    personas = [json.loads(line) for line in src.open(encoding="utf-8")]

    tag = personas[0]["user_id"][0]
    world = World(args.seed, tag)
    users, contacts, txns, devices = [], [], [], []
    for p in personas:
        u, c, t, d = simulate_user(p, world, args.seed)
        users.append(u)
        contacts += c
        txns += t
        devices += d
    for i, t in enumerate(sorted(txns, key=lambda t: (t["ts"], t["user_id"])), 1):
        t["txn_id"] = f"X{tag}{i:06d}"
    txns.sort(key=lambda t: t["txn_id"])

    write_csv(out / "users.csv", users,
              ["user_id", "phone", "city", "segment", "wallet_created_at", "opening_balance_iqd"])
    write_csv(out / "recipients.csv", world.recipients,
              ["recipient_id", "display_name", "phone", "kind", "city", "created_at", "is_biller", "reported"])
    write_csv(out / "contacts.csv", contacts, ["user_id", "recipient_id", "saved_name", "saved_at"])
    write_csv(out / "transactions.csv", txns,
              ["txn_id", "user_id", "ts", "direction", "type", "counterparty_id", "amount_iqd", "balance_after",
               "note", "device_id", "call_active", "reason_tap"])
    write_csv(out / "device_events.csv", devices, ["user_id", "ts", "event", "device_id"])

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
        "personas": src.name, "personas_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "period": [START.isoformat(), END.isoformat()], "ramadan": [d.isoformat() for d in RAMADAN],
        "eids": [EID_FITR.isoformat(), EID_ADHA.isoformat()], "assumptions": A,
        "counts": {"users": len(users), "recipients": len(world.recipients), "contacts": len(contacts),
                   "transactions": len(txns), "device_events": len(devices)},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(json.dumps(manifest["counts"]))


if __name__ == "__main__":
    main()
