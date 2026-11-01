from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from rules.engine import Scorecard  # noqa: E402
from rules.types import Draft, DraftContext, RecipientInfo, Txn  # noqa: E402
from wallet import db  # noqa: E402
from wallet.service import WalletService, hash_pin  # noqa: E402

T0 = datetime(2026, 9, 15, 14, 0)  # a Tuesday afternoon outside Ramadan
NEW = "07801234567"
FAMILY = "07701112222"


def txn(days_ago: float, amount: int, cp: str = FAMILY, direction: str = "out", hour: int = 14,
        kind: str = "p2p", note: str = "", ts: datetime | None = None, name: str = "") -> Txn:
    when = ts or (T0 - timedelta(days=days_ago)).replace(hour=hour, minute=0)
    return Txn(ts=when, direction=direction, counterparty=cp, amount=amount, kind=kind, note=note,
               counterparty_name=name)


def routine_history(n: int = 20, amount: int = 50_000, cp: str = FAMILY) -> tuple[Txn, ...]:
    """n outgoing transfers over the past months, all at 14:00, plus a salary credit."""
    out = [txn(3 + i * 5, amount + (i % 3) * 5_000, cp=cp) for i in range(n)]
    out.append(txn(10, 1_000_000, cp="07500000000", direction="in", kind="salary"))
    return tuple(out)


def make_ctx(amount: int = 50_000, recipient: str = NEW, history: tuple = (), contacts: tuple = (),
             events: tuple = (), balance: int = 1_000_000, recipient_info: RecipientInfo | None = None,
             note: str = "", name: str = "", reason: str | None = None, call: bool = False,
             ts: datetime = T0) -> DraftContext:
    draft = Draft(user_id="u1", recipient_wallet=recipient, amount=amount, ts=ts, recipient_name=name,
                  note=note, reason_code=reason, call_active=call)
    info = recipient_info or RecipientInfo(wallet=recipient, display_name=name, created_at=ts - timedelta(days=400))
    return DraftContext(draft=draft, balance=balance, recipient=info, history=history,
                        contacts=contacts, device_events=events)


@pytest.fixture(scope="session")
def scorecard() -> Scorecard:
    return Scorecard()


class FakeClock:
    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeLLM:
    """Stands in for the vLLM client. `mode`: ok | timeout | error | payload function."""

    def __init__(self, payload=None, mode: str = "ok", delay: float = 0.0):
        self.payload, self.mode, self.delay, self.calls = payload, mode, delay, []

    async def chat_json(self, messages, timeout_s, **_):
        from coach.llm import LLMError

        self.calls.append(messages)
        if self.mode == "timeout":
            await asyncio.sleep(timeout_s + 1)
        if self.mode == "error":
            raise LLMError("http:ConnectError")
        if self.delay:
            await asyncio.sleep(self.delay)
        payload = self.payload(messages) if callable(self.payload) else self.payload
        return payload, 12.0

    async def ping(self, timeout_s: float = 2.0) -> bool:
        return self.mode == "ok"


PIN = "4321"


def seed_user(engine, user_id: str = "u1", balance: int = 1_000_000, history: tuple = (),
              contacts: tuple = ((FAMILY, "حسين علي"),), wallet: str = "07709998888") -> None:
    with engine.begin() as conn:
        conn.execute(db.accounts.insert().values(wallet=wallet, display_name="me", created_at=T0 - timedelta(days=900)))
        conn.execute(db.users.insert().values(id=user_id, wallet=wallet, first_name="سجاد", last_name="",
                                              segment="salaried", city="بغداد", split="test", balance=balance,
                                              pin_hash=hash_pin(PIN)))
        for cw, name in contacts:
            if conn.execute(db.accounts.select().where(db.accounts.c.wallet == cw)).first() is None:
                conn.execute(db.accounts.insert().values(wallet=cw, display_name=name,
                                                         created_at=T0 - timedelta(days=800)))
            conn.execute(db.contacts.insert().values(user_id=user_id, name=name, wallet=cw,
                                                     saved_at=T0 - timedelta(days=300)))
        for t in history:
            conn.execute(db.transactions.insert().values(
                user_id=user_id, direction=t.direction, counterparty=t.counterparty, counterparty_name=t.counterparty_name,
                amount=t.amount, note=t.note, kind=t.kind, status="committed", draft_version=1,
                created_at=t.ts, updated_at=t.ts, committed_at=t.ts))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_service(tmp_path, clock):
    def factory(llm=None, history: tuple = None, **seed_kwargs) -> WalletService:
        settings = Settings.from_env().with_overrides(db_path=str(tmp_path / "w.db"), coach_timeout_s=0.5,
                                                      proceed_lock_s=20, audit_key="test")
        engine = db.make_engine(settings.db_path)
        seed_user(engine, history=routine_history() if history is None else history, **seed_kwargs)
        return WalletService(engine, settings, llm=llm, clock=clock)

    return factory
