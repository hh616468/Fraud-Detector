"""Runtime settings, read from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    coach_timeout_s: float
    proceed_lock_s: int
    db_path: str
    audit_key: str

    @classmethod
    def from_env(cls) -> Settings:
        db_path = os.getenv("DB_PATH", "var/wallet.db")
        if db_path != ":memory:" and not os.path.isabs(db_path):
            db_path = str(ROOT / db_path)
        return cls(
            llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:8070/v1").rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "google/gemma-4-31B-it"),
            llm_api_key=os.getenv("LLM_API_KEY", "EMPTY"),
            coach_timeout_s=float(os.getenv("COACH_TIMEOUT_S", "8")),
            proceed_lock_s=int(os.getenv("PROCEED_LOCK_S", "20")),
            db_path=db_path,
            audit_key=os.getenv("AUDIT_PSEUDONYM_KEY", "dev-only-key"),
        )

    def with_overrides(self, **changes) -> Settings:
        return replace(self, **changes)


settings = Settings.from_env()
