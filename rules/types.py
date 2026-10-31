"""Plain data passed into the detector.

The wallet builds a DraftContext from SQLite; the evaluation builds the same object from
generated JSON. The engine never sees labels, only what a provider could observe before commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Txn:
    ts: datetime
    direction: str  # "out" | "in"
    counterparty: str  # wallet number
    amount: int  # IQD
    counterparty_name: str = ""
    note: str = ""
    kind: str = "p2p"  # p2p | bill | topup | salary | pension | cash_in | merchant


@dataclass(frozen=True)
class Contact:
    name: str
    wallet: str
    saved_at: datetime


@dataclass(frozen=True)
class DeviceEvent:
    ts: datetime
    kind: str  # new_device | pin_reset | sim_change


@dataclass(frozen=True)
class RecipientInfo:
    wallet: str
    display_name: str = ""
    created_at: datetime | None = None
    reported: bool = False
    is_biller: bool = False
    # Provider-side network signal (simulated): distinct unrelated senders in the last 7 days.
    unrelated_senders_7d: int = 0


@dataclass(frozen=True)
class Draft:
    user_id: str
    recipient_wallet: str
    amount: int
    ts: datetime
    recipient_name: str = ""
    note: str = ""
    reason_code: str | None = None
    call_active: bool = False  # simulated call-state flag from the provider's app


@dataclass(frozen=True)
class DraftContext:
    draft: Draft
    balance: int  # balance before this transfer
    recipient: RecipientInfo
    history: tuple[Txn, ...] = field(default_factory=tuple)
    contacts: tuple[Contact, ...] = field(default_factory=tuple)
    device_events: tuple[DeviceEvent, ...] = field(default_factory=tuple)
