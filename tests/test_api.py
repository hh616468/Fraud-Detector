"""HTTP contract the web pages rely on: shapes, status codes, idempotency headers."""

import pytest
from fastapi.testclient import TestClient

from conftest import FAMILY, NEW, PIN, FakeLLM
from wallet.api import create_app

TURN = {
    "concern": "انت هسه على مكالمة ودا تحوّل أغلب رصيدك.",
    "next_tactic": "ممكن يگلك لا تسد الخط.",
    "next_step": "سد الخط واتصل بالرقم الرسمي.",
    "question": "منو طلب منك تدز؟",
    "options": [{"id": "a", "label": "واحد اتصل بيه"}, {"id": "b", "label": "محد"}],
    "cited_rules": ["R07"],
}


@pytest.fixture
def client(make_service):
    svc = make_service(llm=FakeLLM(TURN))
    return TestClient(create_app(svc)), svc


def test_catalogue_and_users(client):
    c, _ = client
    cat = c.get("/api/catalogue").json()
    assert len([r for r in cat["rules"] if r["kind"] == "risk"]) == 13 and cat["reasons"]
    assert c.get("/api/users").json()[0]["id"] == "u1"
    assert c.get("/api/users/nope").status_code == 404


def test_full_coaching_flow_over_http(client, clock):
    c, _ = client
    r = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": 900_000, "call_active": True},
               headers={"Idempotency-Key": "abc"})
    assert r.status_code == 200
    body = r.json()
    tid, ver = body["transfer"]["id"], body["transfer"]["draft_version"]
    assert body["assessment"]["tier"] == "coach" and body["warning"]["text"]
    assert body["assessment"]["proceed_unlock_in_s"] > 0 and len(body["assessment"]["results"]) == 15
    again = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": 900_000,
                                           "call_active": True}, headers={"Idempotency-Key": "abc"}).json()
    assert again["transfer"]["id"] == tid and again["replay"]

    turn = c.post(f"/api/transfers/{tid}/coach", json={"draft_version": ver}).json()
    assert turn["source"] == "llm" and turn["options"]
    locked = c.post(f"/api/transfers/{tid}/confirm", json={"draft_version": ver, "pin": PIN})
    assert locked.status_code == 423 and locked.json()["error"] == "proceed_locked"
    clock.advance(21)
    assert c.post(f"/api/transfers/{tid}/confirm", json={"draft_version": ver, "pin": "0000"}).status_code == 403
    ok = c.post(f"/api/transfers/{tid}/confirm", json={"draft_version": ver, "pin": PIN}, headers={"Idempotency-Key": "k"})
    assert ok.status_code == 200 and ok.json()["transfer"]["status"] == "committed"
    detail = c.get(f"/api/transfers/{tid}").json()
    assert [e["event"] for e in detail["audit"]][0] == "committed" and detail["coach_turns"]
    review = c.get("/api/review").json()
    assert review[0]["transfer_id"] == tid and review[0]["choice"] == "proceed"


def test_edit_then_stale_confirm_is_409(client):
    c, _ = client
    body = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": FAMILY, "amount": 50_000}).json()
    tid = body["transfer"]["id"]
    edited = c.patch(f"/api/transfers/{tid}", json={"draft_version": 1, "amount": 60_000}).json()
    assert edited["transfer"]["draft_version"] == 2
    stale = c.post(f"/api/transfers/{tid}/confirm", json={"draft_version": 1, "pin": PIN})
    assert stale.status_code == 409 and stale.json()["error"] == "stale_draft"


def test_cancel_and_validation(client):
    c, _ = client
    tid = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": 10_000}).json()["transfer"]["id"]
    assert c.post(f"/api/transfers/{tid}/cancel", json={"draft_version": 1}).json()["transfer"]["status"] == "cancelled"
    assert c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": "123", "amount": 1}).status_code == 422
    assert c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": -5}).status_code == 422


def test_console_setup_changes_the_assessment(client):
    c, _ = client
    c.post("/api/console/setup", json={
        "user_id": "u1",
        "device_event": {"kind": "sim_change", "minutes_ago": 30},
        "recipient": {"wallet": NEW, "display_name": "خدمة عملاء زين كاش", "age_days": 1},
    })
    a = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": 100_000,
                                       "recipient_name": "خدمة عملاء زين كاش"}).json()["assessment"]
    assert {"R01", "R06", "R08", "R09"} <= {r["id"] for r in a["fired"]} and a["tier"] == "coach"


def test_patch_can_clear_the_reason(client):
    c, _ = client
    tid = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": 10_000,
                                        "reason_code": "prize_fee"}).json()["transfer"]["id"]
    cleared = c.patch(f"/api/transfers/{tid}", json={"draft_version": 1, "reason_code": None}).json()
    assert cleared["transfer"]["reason_code"] is None
    assert "R13" not in {r["id"] for r in cleared["assessment"]["fired"]}
    untouched = c.patch(f"/api/transfers/{tid}", json={"draft_version": 2, "amount": 12_000}).json()
    assert untouched["transfer"]["reason_code"] is None and untouched["transfer"]["amount"] == 12_000


def test_reviewer_detail_is_pseudonymous(client):
    c, _ = client
    body = c.post("/api/transfers", json={"user_id": "u1", "recipient_wallet": NEW, "amount": 900_000,
                                         "recipient_name": "أبو علي", "note": "secret note", "call_active": True}).json()
    tid = body["transfer"]["id"]
    c.post(f"/api/transfers/{tid}/coach", json={"draft_version": 1})
    detail = c.get(f"/api/review/{tid}").json()
    dumped = str(detail)
    for raw in ("u1", NEW, "أبو علي", "secret note", "سجاد", "900,000"):
        assert raw not in dumped, raw
    assert detail["transfer"]["user_ref"] and detail["transfer"]["has_note"]
