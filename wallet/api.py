"""REST API and static pages for the mock wallet, judge console and reviewer view.

    uvicorn wallet.api:app --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coach.catalogue import families_by_id
from coach.llm import LLMClient
from config import settings as default_settings
from wallet.db import make_engine
from wallet.service import WalletError, WalletService

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class DraftIn(BaseModel):
    user_id: str
    recipient_wallet: str
    amount: int = Field(gt=0)
    recipient_name: str = ""
    note: str = ""
    reason_code: str | None = None
    call_active: bool = False


class EditIn(BaseModel):
    draft_version: int
    amount: int | None = Field(default=None, gt=0)
    recipient_wallet: str | None = None
    recipient_name: str | None = None
    note: str | None = None
    reason_code: str | None = None
    call_active: bool | None = None


class VersionIn(BaseModel):
    draft_version: int


class ConfirmIn(BaseModel):
    draft_version: int
    pin: str


class CoachIn(BaseModel):
    draft_version: int
    chosen_option_id: str | None = None


class DeviceEventIn(BaseModel):
    kind: str = Field(pattern="^(new_device|pin_reset|sim_change)$")
    minutes_ago: int = 30


class RecipientIn(BaseModel):
    wallet: str
    display_name: str = ""
    age_days: float | None = None
    reported: bool = False
    is_biller: bool = False
    is_business: bool = False
    unrelated_senders_7d: int = 0


class CreditIn(BaseModel):
    from_wallet: str
    from_name: str = ""
    amount: int = Field(gt=0)
    minutes_ago: int = 20


class SetupIn(BaseModel):
    user_id: str
    device_event: DeviceEventIn | None = None
    recipient: RecipientIn | None = None
    incoming_credit: CreditIn | None = None


def build_service(settings=default_settings) -> WalletService:
    llm = LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    return WalletService(make_engine(settings.db_path), settings, llm=llm)


def create_app(service: WalletService | None = None) -> FastAPI:
    app = FastAPI(title="Fraud & Scam Protection Agent: mock wallet", version="0.1.0")
    svc = service or build_service()
    app.state.service = svc

    @app.exception_handler(WalletError)
    async def wallet_error(_: Request, err: WalletError):
        return JSONResponse(status_code=err.status, content={"error": err.code, "detail": err.detail, **err.extra})

    @app.get("/api/health")
    async def health():
        llm_ok = await svc.llm.ping() if svc.llm else False
        return {"ok": True, "llm": llm_ok, "rules_version": svc.scorecard.version,
                "fingerprint": svc.scorecard.fingerprint, "coach_timeout_s": svc.settings.coach_timeout_s}

    @app.get("/api/catalogue")
    def catalogue():
        sc = svc.scorecard
        return {
            "version": sc.version, "fingerprint": sc.fingerprint, "tiers": sc.tiers, "overrides": sc.overrides,
            "rules": [{k: r.get(k) for k in ("id", "kind", "points", "en", "ar", "params")} for r in sc.rules],
            "reasons": sc.reasons,
            "families": [{k: f[k] for k in ("id", "en", "ar")} for f in families_by_id().values()],
        }

    @app.get("/api/users")
    def users():
        return svc.list_users()

    @app.get("/api/users/{user_id}")
    def user(user_id: str):
        return svc.get_user(user_id)

    @app.post("/api/transfers")
    def create(body: DraftIn, idempotency_key: str | None = Header(default=None)):
        return svc.create_draft(**body.model_dump(), idempotency_key=idempotency_key)

    @app.get("/api/transfers/{transfer_id}")
    def get(transfer_id: int):
        return svc.get_transfer(transfer_id)

    @app.patch("/api/transfers/{transfer_id}")
    def edit(transfer_id: int, body: EditIn):
        data = body.model_dump()
        version = data.pop("draft_version")
        return svc.update_draft(transfer_id, version, **data)

    @app.post("/api/transfers/{transfer_id}/coach")
    async def coach(transfer_id: int, body: CoachIn):
        return await svc.coach_turn(transfer_id, body.draft_version, body.chosen_option_id)

    @app.post("/api/transfers/{transfer_id}/cancel")
    def cancel(transfer_id: int, body: VersionIn):
        return svc.cancel(transfer_id, body.draft_version)

    @app.post("/api/transfers/{transfer_id}/confirm")
    def confirm(transfer_id: int, body: ConfirmIn, idempotency_key: str | None = Header(default=None)):
        return svc.confirm(transfer_id, body.draft_version, body.pin, idempotency_key)

    @app.get("/api/audit")
    def audit(limit: int = 100, transfer_id: int | None = None):
        return svc.audit(limit=min(limit, 1000), transfer_id=transfer_id)

    @app.get("/api/review")
    def review(limit: int = 100, include_pass: bool = False):
        return svc.review_queue(limit=min(limit, 1000), include_pass=include_pass)

    @app.post("/api/console/setup")
    def console_setup(body: SetupIn):
        return svc.simulate_setup(
            body.user_id,
            device_event=body.device_event.model_dump() if body.device_event else None,
            recipient=body.recipient.model_dump() if body.recipient else None,
            incoming_credit=body.incoming_credit.model_dump() if body.incoming_credit else None,
        )

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/")
        def wallet_page():
            return FileResponse(WEB_DIR / "wallet.html")

        @app.get("/console")
        def console_page():
            return FileResponse(WEB_DIR / "console.html")

        @app.get("/reviewer")
        def reviewer_page():
            return FileResponse(WEB_DIR / "reviewer.html")

    return app


def __getattr__(name: str):
    # `uvicorn wallet.api:app` builds the app on first access, so importing this module in
    # tests does not create the SQLite file.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
