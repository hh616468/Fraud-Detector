"""SQLite schema for the mock wallet, assessments, coaching turns and the audit log."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

metadata = MetaData()

accounts = Table(
    "accounts",
    metadata,
    Column("wallet", String, primary_key=True),
    Column("display_name", String, nullable=False, default=""),
    Column("created_at", DateTime),
    Column("reported", Boolean, nullable=False, default=False),
    Column("is_biller", Boolean, nullable=False, default=False),
    Column("is_business", Boolean, nullable=False, default=False),
    # Provider-side network signal, simulated: distinct unrelated senders in the last 7 days.
    Column("unrelated_senders_7d", Integer, nullable=False, default=0),
)

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("wallet", String, ForeignKey("accounts.wallet"), nullable=False, unique=True),
    Column("first_name", String, nullable=False),
    Column("last_name", String, nullable=False, default=""),
    Column("segment", String, nullable=False, default=""),
    Column("city", String, nullable=False, default=""),
    Column("split", String, nullable=False, default="demo"),
    Column("balance", Integer, nullable=False),
    Column("pin_hash", String, nullable=False),
    Column("persona", JSON),
)

contacts = Table(
    "contacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("wallet", String, nullable=False),
    Column("saved_at", DateTime, nullable=False),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.id"), nullable=False),
    Column("direction", String, nullable=False),  # out | in
    Column("counterparty", String, nullable=False),
    Column("counterparty_name", String, nullable=False, default=""),
    Column("amount", Integer, nullable=False),
    Column("note", Text, nullable=False, default=""),
    Column("kind", String, nullable=False, default="p2p"),
    Column("status", String, nullable=False),  # pending | committed | cancelled
    Column("draft_version", Integer, nullable=False, default=1),
    Column("reason_code", String),
    Column("call_active", Boolean, nullable=False, default=False),
    Column("idempotency_key", String),
    Column("confirm_key", String),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("committed_at", DateTime),
    UniqueConstraint("user_id", "idempotency_key", name="uq_draft_idempotency"),
    sqlite_autoincrement=True,
)

device_events = Table(
    "device_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.id"), nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("ts", DateTime, nullable=False),
)

assessments = Table(
    "assessments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("transfer_id", Integer, ForeignKey("transactions.id"), nullable=False, index=True),
    Column("draft_version", Integer, nullable=False),
    Column("score", Integer, nullable=False),
    Column("tier", String, nullable=False),
    Column("override", String),
    Column("fired_ids", JSON, nullable=False),
    Column("results", JSON, nullable=False),
    Column("family", String, nullable=False),
    Column("warning_text", Text),
    Column("facts", JSON, nullable=False),
    Column("rules_version", String, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("proceed_unlock_at", DateTime, nullable=False),
    Column("superseded", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, nullable=False),
    sqlite_autoincrement=True,
)

coach_turns = Table(
    "coach_turns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("assessment_id", Integer, ForeignKey("assessments.id"), nullable=False, index=True),
    Column("turn", Integer, nullable=False),
    Column("source", String, nullable=False),  # llm | template
    Column("payload", JSON, nullable=False),
    Column("chosen_option", String),
    Column("latency_ms", Float),
    Column("errors", JSON),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("assessment_id", "turn", name="uq_turn_per_assessment"),
    sqlite_autoincrement=True,
)

decisions = Table(
    "decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("transfer_id", Integer, ForeignKey("transactions.id"), nullable=False, index=True),
    Column("assessment_id", Integer, ForeignKey("assessments.id")),
    Column("draft_version", Integer, nullable=False),
    Column("choice", String, nullable=False),  # cancel | proceed
    Column("outcome", String, nullable=False),  # cancelled | committed
    Column("created_at", DateTime, nullable=False),
    sqlite_autoincrement=True,
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime, nullable=False),
    Column("event", String, nullable=False),
    Column("user_ref", String, nullable=False),  # HMAC pseudonym, never the raw id
    Column("transfer_id", Integer),
    Column("payload", JSON, nullable=False),
    sqlite_autoincrement=True,
)


def make_engine(db_path: str) -> Engine:
    if db_path == ":memory:":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 15}
        )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        # Let SQLAlchemy, not pysqlite, decide when transactions begin (see _begin_immediate).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        if db_path != ":memory:":
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        # Take the write lock up front so concurrent confirms queue instead of failing on a
        # stale snapshot; the conditional UPDATE then guarantees a single commit.
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    metadata.create_all(engine)
    return engine
