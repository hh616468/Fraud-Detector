"""Transfer state machine for the mock wallet.

draft (pending, no money moves) -> assess -> template warning -> optional coaching turns
-> cancel (never debits) | confirm (PIN + current draft version, commits exactly once).
Editing amount, recipient, note or reason bumps the draft version and needs a fresh assessment.
Every step lands in the append-only audit log under a pseudonymous user reference.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from coach import templates as tpl
from coach.catalogue import families_by_id, match_family
from coach.llm import LLMClient
from coach.session import MAX_TURNS, CoachRequest, amount_band, next_turn
from config import Settings
from rules.engine import Scorecard, serialize_values
from rules.types import Contact, DeviceEvent, Draft, DraftContext, RecipientInfo, Txn
from wallet import db

BAGHDAD = ZoneInfo("Asia/Baghdad")
WALLET_RE = re.compile(r"^07[3-9]\d{8}$")
EDITABLE = ("amount", "recipient_wallet", "recipient_name", "note", "reason_code", "call_active")
MAX_NOTE = 140
MAX_NAME = 60


def now_baghdad() -> datetime:
    return datetime.now(BAGHDAD).replace(tzinfo=None)


class WalletError(Exception):
    status = 400

    def __init__(self, code: str, detail: str = "", **extra):
        super().__init__(code)
        self.code, self.detail, self.extra = code, detail, extra


class NotFound(WalletError):
    status = 404


class Conflict(WalletError):
    status = 409


class Forbidden(WalletError):
    status = 403


class Locked(WalletError):
    status = 423


class Invalid(WalletError):
    status = 422


def hash_pin(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    return f"{salt}${hashlib.sha256((salt + pin).encode()).hexdigest()}"


def check_pin(pin: str, stored: str) -> bool:
    salt, _, digest = stored.partition("$")
    return hmac.compare_digest(hash_pin(pin, salt).partition("$")[2], digest)


class WalletService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        scorecard: Scorecard | None = None,
        llm: LLMClient | None = None,
        clock=now_baghdad,
    ):
        self.engine = engine
        self.settings = settings
        self.scorecard = scorecard or Scorecard()
        self.llm = llm
        self.clock = clock
        self.reasons = {r["code"]: r for r in self.scorecard.reasons}

    # ---------- helpers ----------

    def pseudonym(self, user_id: str) -> str:
        return hmac.new(self.settings.audit_key.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:12]

    def _audit(self, conn: Connection, event: str, user_id: str, transfer_id: int | None, payload: dict) -> None:
        conn.execute(
            insert(db.audit_log).values(
                ts=self.clock(), event=event, user_ref=self.pseudonym(user_id), transfer_id=transfer_id, payload=payload
            )
        )

    def _audit_standalone(self, event: str, user_id: str, transfer_id: int | None, payload: dict) -> None:
        with self.engine.begin() as conn:
            self._audit(conn, event, user_id, transfer_id, payload)

    @staticmethod
    def _one(conn: Connection, table, *where):
        return conn.execute(select(table).where(*where)).mappings().first()

    def _user(self, conn: Connection, user_id: str):
        row = self._one(conn, db.users, db.users.c.id == user_id)
        if row is None:
            raise NotFound("user_not_found", user_id)
        return row

    def _transfer(self, conn: Connection, transfer_id: int):
        row = self._one(conn, db.transactions, db.transactions.c.id == transfer_id, db.transactions.c.direction == "out")
        if row is None:
            raise NotFound("transfer_not_found", str(transfer_id))
        return row

    def _current_assessment(self, conn: Connection, transfer_id: int):
        return (
            conn.execute(
                select(db.assessments)
                .where(db.assessments.c.transfer_id == transfer_id, db.assessments.c.superseded.is_(False))
                .order_by(db.assessments.c.id.desc())
            )
            .mappings()
            .first()
        )

    def _validate_fields(self, f: dict) -> None:
        if "amount" in f and (not isinstance(f["amount"], int) or f["amount"] <= 0):
            raise Invalid("invalid_amount", "amount must be a positive whole number of IQD")
        if "recipient_wallet" in f and not WALLET_RE.match(f["recipient_wallet"] or ""):
            raise Invalid("invalid_wallet", "wallet numbers look like 07XXXXXXXXX")
        if len(f.get("note") or "") > MAX_NOTE:
            raise Invalid("note_too_long")
        if len(f.get("recipient_name") or "") > MAX_NAME:
            raise Invalid("name_too_long")
        if f.get("reason_code") and f["reason_code"] not in self.reasons:
            raise Invalid("unknown_reason")

    def _ensure_account(self, conn: Connection, wallet: str, name: str) -> None:
        if self._one(conn, db.accounts, db.accounts.c.wallet == wallet) is None:
            # Unknown to the mock provider: no creation date, so R08's age check stays silent.
            conn.execute(insert(db.accounts).values(wallet=wallet, display_name=name or "", created_at=None))

    # ---------- detection ----------

    def build_context(self, conn: Connection, t, ts: datetime) -> DraftContext:
        user = self._user(conn, t["user_id"])
        hist = conn.execute(
            select(db.transactions).where(
                db.transactions.c.user_id == t["user_id"],
                db.transactions.c.status == "committed",
                db.transactions.c.created_at < ts,
            )
        ).mappings()
        history = tuple(
            Txn(
                ts=h["committed_at"] or h["created_at"],
                direction=h["direction"],
                counterparty=h["counterparty"],
                amount=h["amount"],
                counterparty_name=h["counterparty_name"],
                note=h["note"],
                kind=h["kind"],
            )
            for h in hist
        )
        contacts = tuple(
            Contact(name=c["name"], wallet=c["wallet"], saved_at=c["saved_at"])
            for c in conn.execute(select(db.contacts).where(db.contacts.c.user_id == t["user_id"])).mappings()
        )
        events = tuple(
            DeviceEvent(ts=e["ts"], kind=e["kind"])
            for e in conn.execute(select(db.device_events).where(db.device_events.c.user_id == t["user_id"])).mappings()
        )
        acc = self._one(conn, db.accounts, db.accounts.c.wallet == t["counterparty"])
        recipient = RecipientInfo(
            wallet=t["counterparty"],
            display_name=acc["display_name"] if acc else "",
            created_at=acc["created_at"] if acc else None,
            reported=bool(acc and acc["reported"]),
            is_biller=bool(acc and acc["is_biller"]),
            is_business=bool(acc and acc["is_business"]),
            unrelated_senders_7d=acc["unrelated_senders_7d"] if acc else 0,
        )
        draft = Draft(
            user_id=t["user_id"],
            recipient_wallet=t["counterparty"],
            amount=t["amount"],
            ts=ts,
            recipient_name=t["counterparty_name"],
            note=t["note"],
            reason_code=t["reason_code"],
            call_active=bool(t["call_active"]),
        )
        return DraftContext(
            draft=draft, balance=user["balance"], recipient=recipient,
            history=history, contacts=contacts, device_events=events,
        )

    def _assess(self, conn: Connection, t, now: datetime):
        ctx = self.build_context(conn, t, now)
        a = self.scorecard.assess(ctx)
        fired = [
            {"id": r.id, "points": r.points, "en": r.en, "ar": r.ar, "values": serialize_values(r.values)}
            for r in a.fired
        ]
        family = match_family({r["id"]: r["values"] for r in fired}, t["reason_code"], t["note"])
        unlock = now + timedelta(seconds=self.settings.proceed_lock_s) if a.tier == "coach" else now
        conn.execute(
            update(db.assessments)
            .where(db.assessments.c.transfer_id == t["id"], db.assessments.c.superseded.is_(False))
            .values(superseded=True)
        )
        values = dict(
            transfer_id=t["id"],
            draft_version=t["draft_version"],
            score=a.score,
            tier=a.tier,
            override=a.override,
            fired_ids=a.fired_ids,
            results=[{**r.__dict__, "values": serialize_values(r.values)} for r in a.results],
            family=family["id"],
            warning_text=tpl.build_warning(a.tier, fired, family),
            facts={"amount_band": amount_band(t["amount"], ctx.balance, a.fired_ids), "balance_before": ctx.balance},
            rules_version=a.rules_version,
            fingerprint=a.fingerprint,
            latency_ms=a.latency_ms,
            proceed_unlock_at=unlock,
            superseded=False,
            created_at=now,
        )
        aid = conn.execute(insert(db.assessments).values(**values)).inserted_primary_key[0]
        self._audit(conn, "assessed", t["user_id"], t["id"], {
            "assessment_id": aid, "draft_version": t["draft_version"], "score": a.score, "tier": a.tier,
            "fired": a.fired_ids, "override": a.override, "family": family["id"], "rules_version": a.rules_version,
            "fingerprint": a.fingerprint, "latency_ms": a.latency_ms,
        })
        if a.tier != "pass":
            self._audit(conn, "warning_shown", t["user_id"], t["id"], {"assessment_id": aid, "source": "template"})
        return self._one(conn, db.assessments, db.assessments.c.id == aid)

    def _fill_values(self, conn: Connection, t) -> dict[str, str]:
        user = self._user(conn, t["user_id"])
        return {
            "amount": tpl.format_iqd(t["amount"]),
            "recipient": t["counterparty_name"] or t["counterparty"],
            "first_name": user["first_name"],
        }

    def _view(self, conn: Connection, t, a=None, replay: bool = False, pseudonymous: bool = False) -> dict:
        a = a or self._current_assessment(conn, t["id"])
        fill = {} if pseudonymous else self._fill_values(conn, t)
        transfer = {
            "id": t["id"], "amount": t["amount"], "reason_code": t["reason_code"],
            "call_active": bool(t["call_active"]), "status": t["status"], "draft_version": t["draft_version"],
            "committed_at": t["committed_at"].isoformat() if t["committed_at"] else None,
        }
        if pseudonymous:
            transfer.update(user_ref=self.pseudonym(t["user_id"]), recipient_ref=self.pseudonym(t["counterparty"]),
                            has_note=bool(t["note"]))
        else:
            transfer.update(user_id=t["user_id"], recipient_wallet=t["counterparty"],
                            recipient_name=t["counterparty_name"], note=t["note"])
        view = {"transfer": transfer, "replay": replay}
        if a is not None:
            fired = [r for r in a["results"] if r["fired"]]
            family = families_by_id().get(a["family"], families_by_id()["generic"])
            view["assessment"] = {
                "id": a["id"], "draft_version": a["draft_version"], "score": a["score"], "tier": a["tier"],
                "override": a["override"], "fired": fired, "family": {"id": family["id"], "ar": family["ar"], "en": family["en"]},
                "rules_version": a["rules_version"], "latency_ms": a["latency_ms"],
                "proceed_unlock_at": a["proceed_unlock_at"].isoformat(),
                "proceed_unlock_in_s": max(0.0, round((a["proceed_unlock_at"] - self.clock()).total_seconds(), 1)),
                "results": a["results"],
                "superseded": a["superseded"],
                "is_new_recipient": "R01" in a["fired_ids"],
            }
            view["warning"] = (
                {"tier": a["tier"], "text": tpl.fill_placeholders(a["warning_text"], fill), "source": "template"}
                if a["warning_text"] else None
            )
        return view

    # ---------- queries ----------

    def list_users(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.users).order_by(db.users.c.id)).mappings()
            return [
                {k: r[k] for k in ("id", "first_name", "last_name", "segment", "city", "split", "balance", "wallet")}
                for r in rows
            ]

    def get_user(self, user_id: str) -> dict:
        with self.engine.connect() as conn:
            u = self._user(conn, user_id)
            contacts = conn.execute(
                select(db.contacts).where(db.contacts.c.user_id == user_id).order_by(db.contacts.c.name)
            ).mappings()
            recent = conn.execute(
                select(db.transactions)
                .where(db.transactions.c.user_id == user_id, db.transactions.c.status == "committed")
                .order_by(db.transactions.c.created_at.desc())
                .limit(15)
            ).mappings()
            return {
                **{k: u[k] for k in ("id", "first_name", "last_name", "segment", "city", "balance", "wallet")},
                "contacts": [{"name": c["name"], "wallet": c["wallet"]} for c in contacts],
                "recent": [
                    {"ts": r["created_at"].isoformat(), "direction": r["direction"], "name": r["counterparty_name"],
                     "wallet": r["counterparty"], "amount": r["amount"], "kind": r["kind"]}
                    for r in recent
                ],
            }

    def get_transfer(self, transfer_id: int, pseudonymous: bool = False) -> dict:
        """Full transfer view. pseudonymous=True is the reviewer's view: refs instead of ids and names."""
        with self.engine.connect() as conn:
            t = self._transfer(conn, transfer_id)
            view = self._view(conn, t, pseudonymous=pseudonymous)
            if "assessment" in view:
                turns = conn.execute(
                    select(db.coach_turns)
                    .where(db.coach_turns.c.assessment_id == view["assessment"]["id"])
                    .order_by(db.coach_turns.c.turn)
                ).mappings()
                fill = {} if pseudonymous else self._fill_values(conn, t)
                view["coach_turns"] = [self._turn_view(r, fill) for r in turns]
            view["audit"] = self._audit_rows(conn, transfer_id=transfer_id, limit=200)
            return view

    def _audit_rows(self, conn: Connection, transfer_id: int | None = None, limit: int = 100) -> list[dict]:
        q = select(db.audit_log).order_by(db.audit_log.c.id.desc()).limit(limit)
        if transfer_id is not None:
            q = q.where(db.audit_log.c.transfer_id == transfer_id)
        return [
            {"id": r["id"], "ts": r["ts"].isoformat(), "event": r["event"], "user_ref": r["user_ref"],
             "transfer_id": r["transfer_id"], "payload": r["payload"]}
            for r in conn.execute(q).mappings()
        ]

    def audit(self, limit: int = 100, transfer_id: int | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            return self._audit_rows(conn, transfer_id, limit)

    def review_queue(self, limit: int = 100, include_pass: bool = False) -> list[dict]:
        """One row per transfer for the reviewer: latest assessment, coaching source, choice, outcome."""
        with self.engine.connect() as conn:
            q = (
                select(db.transactions, db.assessments.c.id.label("aid"), db.assessments.c.score,
                       db.assessments.c.tier, db.assessments.c.fired_ids, db.assessments.c.family,
                       db.assessments.c.override)
                .join(db.assessments, and_(db.assessments.c.transfer_id == db.transactions.c.id,
                                           db.assessments.c.superseded.is_(False)))
                .order_by(db.transactions.c.id.desc())
                .limit(limit)
            )
            if not include_pass:
                q = q.where(db.assessments.c.tier != "pass")
            out = []
            for r in conn.execute(q).mappings():
                turns = conn.execute(
                    select(db.coach_turns.c.source, db.coach_turns.c.chosen_option)
                    .where(db.coach_turns.c.assessment_id == r["aid"])
                ).all()
                decision = conn.execute(
                    select(db.decisions.c.choice).where(db.decisions.c.transfer_id == r["id"])
                    .order_by(db.decisions.c.id.desc())
                ).first()
                out.append({
                    "transfer_id": r["id"], "user_ref": self.pseudonym(r["user_id"]), "ts": r["created_at"].isoformat(),
                    "amount": r["amount"], "status": r["status"], "draft_version": r["draft_version"],
                    "score": r["score"], "tier": r["tier"], "fired": r["fired_ids"], "family": r["family"],
                    "override": r["override"], "coach_sources": [s for s, _ in turns],
                    "taps": [c for _, c in turns if c], "choice": decision[0] if decision else None,
                })
            return out

    # ---------- commands ----------

    def create_draft(
        self,
        user_id: str,
        recipient_wallet: str,
        amount: int,
        recipient_name: str = "",
        note: str = "",
        reason_code: str | None = None,
        call_active: bool = False,
        idempotency_key: str | None = None,
    ) -> dict:
        self._validate_fields(dict(amount=amount, recipient_wallet=recipient_wallet, recipient_name=recipient_name,
                                   note=note, reason_code=reason_code))
        try:
            with self.engine.begin() as conn:
                user = self._user(conn, user_id)
                if idempotency_key:
                    existing = self._one(conn, db.transactions, db.transactions.c.user_id == user_id,
                                         db.transactions.c.idempotency_key == idempotency_key)
                    if existing is not None:
                        return self._view(conn, existing, replay=True)
                if recipient_wallet == user["wallet"]:
                    raise Invalid("self_transfer")
                self._ensure_account(conn, recipient_wallet, recipient_name)
                now = self.clock()
                tid = conn.execute(insert(db.transactions).values(
                    user_id=user_id, direction="out", counterparty=recipient_wallet,
                    counterparty_name=recipient_name or "", amount=amount, note=note or "", kind="p2p",
                    status="pending", draft_version=1, reason_code=reason_code or None, call_active=call_active,
                    idempotency_key=idempotency_key, created_at=now, updated_at=now,
                )).inserted_primary_key[0]
                t = self._transfer(conn, tid)
                self._audit(conn, "draft_created", user_id, tid, {
                    "amount": amount, "recipient_ref": self.pseudonym(recipient_wallet), "reason": reason_code,
                    "call_active": call_active, "draft_version": 1,
                })
                a = self._assess(conn, t, now)
                return self._view(conn, t, a)
        except IntegrityError as exc:  # concurrent create with the same idempotency key
            with self.engine.connect() as conn:
                existing = self._one(conn, db.transactions, db.transactions.c.user_id == user_id,
                                     db.transactions.c.idempotency_key == idempotency_key)
                if existing is None:
                    raise Conflict("integrity_error") from exc
                return self._view(conn, existing, replay=True)

    def update_draft(self, transfer_id: int, draft_version: int, **changes) -> dict:
        # reason_code may be cleared (None or ""); other fields ignore None.
        changes = {k: v for k, v in changes.items() if k in EDITABLE and (v is not None or k == "reason_code")}
        if "reason_code" in changes:
            changes["reason_code"] = changes["reason_code"] or None
        if not changes:
            raise Invalid("nothing_to_change")
        self._validate_fields(changes)
        with self.engine.begin() as conn:
            t = self._transfer(conn, transfer_id)
            if t["status"] != "pending":
                raise Conflict(f"transfer_{t['status']}")
            if t["draft_version"] != draft_version:
                raise Conflict("stale_draft", current_version=t["draft_version"])
            now = self.clock()
            values = {"draft_version": draft_version + 1, "updated_at": now}
            column = {"recipient_wallet": "counterparty", "recipient_name": "counterparty_name"}
            for k, v in changes.items():
                values[column.get(k, k)] = v
            if "recipient_wallet" in changes:
                self._ensure_account(conn, changes["recipient_wallet"], changes.get("recipient_name", ""))
            conn.execute(update(db.transactions).where(db.transactions.c.id == transfer_id).values(**values))
            self._audit(conn, "draft_edited", t["user_id"], transfer_id, {
                "from_version": draft_version, "to_version": draft_version + 1, "changed": sorted(changes),
                "consent_reset": True,
            })
            t = self._transfer(conn, transfer_id)
            a = self._assess(conn, t, now)
            return self._view(conn, t, a)

    def cancel(self, transfer_id: int, draft_version: int | None = None) -> dict:
        with self.engine.begin() as conn:
            t = self._transfer(conn, transfer_id)
            if t["status"] == "cancelled":
                return self._view(conn, t, replay=True)
            if t["status"] == "committed":
                raise Conflict("transfer_committed")
            a = self._current_assessment(conn, transfer_id)
            now = self.clock()
            conn.execute(update(db.transactions).where(
                db.transactions.c.id == transfer_id, db.transactions.c.status == "pending"
            ).values(status="cancelled", updated_at=now))
            conn.execute(insert(db.decisions).values(
                transfer_id=transfer_id, assessment_id=a["id"] if a else None, draft_version=t["draft_version"],
                choice="cancel", outcome="cancelled", created_at=now,
            ))
            self._audit(conn, "cancelled", t["user_id"], transfer_id, {
                "draft_version": t["draft_version"], "tier": a["tier"] if a else None, "debited": False,
            })
            return self._view(conn, self._transfer(conn, transfer_id), a)

    def confirm(self, transfer_id: int, draft_version: int, pin: str, confirm_key: str | None = None) -> dict:
        try:
            with self.engine.begin() as conn:
                t = self._transfer(conn, transfer_id)
                if t["status"] == "committed":
                    if confirm_key and t["confirm_key"] == confirm_key:
                        return self._view(conn, t, replay=True)
                    raise Conflict("already_committed")
                if t["status"] == "cancelled":
                    raise Conflict("transfer_cancelled")
                if t["draft_version"] != draft_version:
                    raise Conflict("stale_draft", "the transfer changed; review the new warning",
                                   current_version=t["draft_version"])
                a = self._current_assessment(conn, transfer_id)
                if a is None or a["draft_version"] != draft_version:
                    raise Conflict("not_assessed")
                now = self.clock()
                if now < a["proceed_unlock_at"]:
                    wait = (a["proceed_unlock_at"] - now).total_seconds()
                    raise Locked("proceed_locked", f"Proceed unlocks in {wait:.0f} s", seconds=round(wait, 1))
                user = self._user(conn, t["user_id"])
                if not check_pin(pin, user["pin_hash"]):
                    raise Forbidden("bad_pin")
                res = conn.execute(update(db.transactions).where(
                    db.transactions.c.id == transfer_id,
                    db.transactions.c.status == "pending",
                    db.transactions.c.draft_version == draft_version,
                ).values(status="committed", committed_at=now, updated_at=now, confirm_key=confirm_key))
                if res.rowcount != 1:
                    raise Conflict("concurrent_commit")
                debit = conn.execute(update(db.users).where(
                    db.users.c.id == t["user_id"], db.users.c.balance >= t["amount"]
                ).values(balance=db.users.c.balance - t["amount"]))
                if debit.rowcount != 1:
                    raise Conflict("insufficient_funds")
                conn.execute(insert(db.decisions).values(
                    transfer_id=transfer_id, assessment_id=a["id"], draft_version=draft_version,
                    choice="proceed", outcome="committed", created_at=now,
                ))
                self._audit(conn, "committed", t["user_id"], transfer_id, {
                    "draft_version": draft_version, "tier": a["tier"], "amount": t["amount"],
                    "balance_after": user["balance"] - t["amount"],
                })
                return self._view(conn, self._transfer(conn, transfer_id), a)
        except WalletError as err:
            if err.code not in ("already_committed",):
                with self.engine.connect() as conn:
                    row = self._one(conn, db.transactions, db.transactions.c.id == transfer_id)
                if row is not None:
                    self._audit_standalone("confirm_rejected", row["user_id"], transfer_id,
                                           {"reason": err.code, "draft_version": draft_version})
            raise

    # ---------- coaching ----------

    @staticmethod
    def _turn_view(row, fill: dict[str, str]) -> dict:
        p = row["payload"]
        f = lambda s: tpl.fill_placeholders(s or "", fill)  # noqa: E731
        return {
            "turn": row["turn"], "final": row["turn"] >= MAX_TURNS, "source": row["source"],
            "latency_ms": row["latency_ms"], "errors": row["errors"] or [], "chosen_option": row["chosen_option"],
            "concern": f(p.get("concern")), "next_tactic": f(p.get("next_tactic")), "next_step": f(p.get("next_step")),
            "question": f(p.get("question")), "cited_rules": p.get("cited_rules", []),
            "options": [{"id": o["id"], "label": f(o["label"])} for o in p.get("options", [])],
        }

    async def coach_turn(self, transfer_id: int, draft_version: int, chosen_option_id: str | None = None) -> dict:
        with self.engine.begin() as conn:
            t = self._transfer(conn, transfer_id)
            if t["status"] != "pending":
                raise Conflict(f"transfer_{t['status']}")
            if t["draft_version"] != draft_version:
                raise Conflict("stale_draft", current_version=t["draft_version"])
            a = self._current_assessment(conn, transfer_id)
            if a is None or a["tier"] != "coach":
                raise Conflict("no_coaching_at_this_tier")
            turns = list(conn.execute(
                select(db.coach_turns).where(db.coach_turns.c.assessment_id == a["id"]).order_by(db.coach_turns.c.turn)
            ).mappings())
            if len(turns) >= MAX_TURNS:
                raise Conflict("chat_finished")
            conversation = []
            for prev in turns[:-1]:
                label = next((o["label"] for o in prev["payload"].get("options", []) if o["id"] == prev["chosen_option"]), "")
                conversation.append({"question": prev["payload"].get("question", ""), "tapped": label})
            if turns:
                last = turns[-1]
                options = {o["id"]: o["label"] for o in last["payload"].get("options", [])}
                if chosen_option_id not in options:
                    raise Invalid("choose_an_option", options=list(options))
                conn.execute(update(db.coach_turns).where(db.coach_turns.c.id == last["id"]).values(
                    chosen_option=chosen_option_id))
                conversation.append({"question": last["payload"].get("question", ""), "tapped": options[chosen_option_id]})
                self._audit(conn, "tap_chosen", t["user_id"], transfer_id, {
                    "assessment_id": a["id"], "turn": last["turn"], "option": chosen_option_id,
                })
            fired = [r for r in a["results"] if r["fired"]]
            req = CoachRequest(
                turn=len(turns) + 1, fired=fired, family=families_by_id().get(a["family"], families_by_id()["generic"]),
                amount_band=a["facts"]["amount_band"], conversation=conversation, last_tap_id=chosen_option_id,
            )
            user_id, aid = t["user_id"], a["id"]

        # The model call happens outside any DB transaction; the wallet stays usable meanwhile.
        result = await next_turn(req, self.llm, self.settings.coach_timeout_s)

        try:
            with self.engine.begin() as conn:
                tid = conn.execute(insert(db.coach_turns).values(
                    assessment_id=aid, turn=req.turn, source=result.source, payload=result.payload,
                    latency_ms=result.latency_ms, errors=result.errors, created_at=self.clock(),
                )).inserted_primary_key[0]
                self._audit(conn, "coach_turn", user_id, transfer_id, {
                    "assessment_id": aid, "turn": req.turn, "source": result.source,
                    "latency_ms": result.latency_ms, "errors": result.errors,
                    "cited_rules": result.payload.get("cited_rules", []),
                })
                row = self._one(conn, db.coach_turns, db.coach_turns.c.id == tid)
                return self._turn_view(row, self._fill_values(conn, self._transfer(conn, transfer_id)))
        except IntegrityError as exc:
            raise Conflict("turn_already_generated") from exc

    # ---------- judge console ----------

    def simulate_setup(
        self,
        user_id: str,
        device_event: dict | None = None,
        recipient: dict | None = None,
        incoming_credit: dict | None = None,
    ) -> dict:
        """Create the provider-side conditions of a scenario (events, recipient facts, a stranger credit)."""
        now = self.clock()
        with self.engine.begin() as conn:
            self._user(conn, user_id)
            done = []
            if device_event:
                ts = now - timedelta(minutes=int(device_event.get("minutes_ago", 30)))
                conn.execute(insert(db.device_events).values(user_id=user_id, kind=device_event["kind"], ts=ts))
                done.append(f"device_event:{device_event['kind']}")
            if recipient:
                self._validate_fields({"recipient_wallet": recipient["wallet"]})
                created = None
                if recipient.get("age_days") is not None:
                    created = now - timedelta(days=float(recipient["age_days"]))
                values = dict(
                    display_name=recipient.get("display_name", ""), created_at=created,
                    reported=bool(recipient.get("reported")), is_biller=bool(recipient.get("is_biller")),
                    is_business=bool(recipient.get("is_business")),
                    unrelated_senders_7d=int(recipient.get("unrelated_senders_7d", 0)),
                )
                if self._one(conn, db.accounts, db.accounts.c.wallet == recipient["wallet"]) is None:
                    conn.execute(insert(db.accounts).values(wallet=recipient["wallet"], **values))
                else:
                    conn.execute(update(db.accounts).where(db.accounts.c.wallet == recipient["wallet"]).values(**values))
                done.append("recipient")
            if incoming_credit:
                self._validate_fields({"recipient_wallet": incoming_credit["from_wallet"],
                                       "amount": int(incoming_credit["amount"])})
                ts = now - timedelta(minutes=int(incoming_credit.get("minutes_ago", 20)))
                self._ensure_account(conn, incoming_credit["from_wallet"], incoming_credit.get("from_name", ""))
                conn.execute(insert(db.transactions).values(
                    user_id=user_id, direction="in", counterparty=incoming_credit["from_wallet"],
                    counterparty_name=incoming_credit.get("from_name", ""), amount=int(incoming_credit["amount"]),
                    note="", kind="p2p", status="committed", draft_version=1, created_at=ts, updated_at=ts,
                    committed_at=ts,
                ))
                conn.execute(update(db.users).where(db.users.c.id == user_id).values(
                    balance=db.users.c.balance + int(incoming_credit["amount"])))
                done.append("incoming_credit")
            self._audit(conn, "console_setup", user_id, None, {"applied": done})
            return {"applied": done}
