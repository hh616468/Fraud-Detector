"""Load generated decisions and rebuild the exact DraftContext the detector would have seen."""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from rules.types import Contact, DeviceEvent, Draft, DraftContext, RecipientInfo, Txn

GENERATED = Path(__file__).resolve().parent / "generated"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _txn(t: dict) -> Txn:
    return Txn(ts=_dt(t["ts"]), direction=t["direction"], counterparty=t["counterparty"], amount=t["amount"],
               counterparty_name=t.get("counterparty_name", ""), note=t.get("note", ""), kind=t.get("kind", "p2p"))


class UserData:
    def __init__(self, raw: dict):
        self.raw = raw
        self.id = raw["user"]["id"]
        self.history = [_txn(t) for t in raw["history"]]
        self.contacts = tuple(Contact(name=c["name"], wallet=c["wallet"], saved_at=_dt(c["saved_at"]))
                              for c in raw["contacts"])
        self.events = [DeviceEvent(ts=_dt(e["ts"]), kind=e["kind"]) for e in raw["device_events"]]
        self.start_balance = raw["start_balance"]


@lru_cache(maxsize=8)
def load_users(split: str) -> dict[str, UserData]:
    users = {}
    for f in sorted((GENERATED / split / "users").glob("*.json")):
        u = UserData(json.loads(f.read_text(encoding="utf-8")))
        users[u.id] = u
    return users


def load_decisions(split: str) -> list[dict]:
    path = GENERATED / split / "decisions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def decision_context(dec: dict, user: UserData) -> DraftContext:
    ts = _dt(dec["ts"])
    extra = [_txn(t) for t in dec["setup"].get("extra_history", [])]
    history = [t for t in user.history if t.ts < ts] + [t for t in extra if t.ts < ts]
    balance = user.start_balance + sum(t.amount if t.direction == "in" else -t.amount for t in history)
    events = [e for e in user.events if e.ts < ts]
    events += [DeviceEvent(ts=_dt(e["ts"]), kind=e["kind"]) for e in dec["setup"].get("device_events", [])]
    d, r = dec["draft"], dec["recipient"]
    return DraftContext(
        draft=Draft(user_id=dec["user_id"], recipient_wallet=d["recipient_wallet"], amount=d["amount"], ts=ts,
                    recipient_name=d.get("recipient_name", ""), note=d.get("note", ""),
                    reason_code=d.get("reason_code"), call_active=bool(d.get("call_active"))),
        balance=balance,
        recipient=RecipientInfo(wallet=r["wallet"], display_name=r.get("display_name", ""),
                                created_at=_dt(r.get("created_at")), reported=bool(r.get("reported")),
                                is_biller=bool(r.get("is_biller")), is_business=bool(r.get("is_business")),
                                unrelated_senders_7d=int(r.get("unrelated_senders_7d", 0))),
        history=tuple(history),
        contacts=user.contacts,
        device_events=tuple(events),
    )
