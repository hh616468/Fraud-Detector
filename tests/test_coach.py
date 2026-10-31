"""Validator, family matching and deterministic templates."""

import asyncio

from coach.catalogue import families_by_id, match_family
from coach.session import CoachRequest, next_turn
from coach.templates import build_warning, fill_placeholders
from coach.validator import validate_turn
from conftest import FakeLLM

GOOD = {
    "concern": "إذا طالبين منك رسوم حتى تستلم جائزة، هاي علامة احتيال.",
    "next_tactic": "ممكن بعدها يطلبون رسوم ثانية.",
    "next_step": "اتصل بالرقم الرسمي وتأكد.",
    "question": "منو گلك ربحت؟",
    "options": [{"id": "a", "label": "رسالة"}, {"id": "b", "label": "مكالمة"}],
    "cited_rules": ["R10"],
}


def errs(turn, fired=("R10", "R01"), family="prize_fee", final=False, extra=frozenset()):
    return validate_turn(turn, list(fired), family, final, extra)


def test_valid_turn_passes():
    assert errs(GOOD) == []


def test_digits_rejected_latin_and_arabic():
    assert any(e.startswith("digits") for e in errs({**GOOD, "concern": "حولت 500 ألف"}))
    assert any(e.startswith("digits") for e in errs({**GOOD, "concern": "حولت ٥٠٠ ألف"}))


def test_uncited_or_unfired_rules_rejected():
    assert "cited_rules:not_fired" in errs({**GOOD, "cited_rules": ["R09"]})
    assert "cited_rules:empty" in errs({**GOOD, "cited_rules": []})


def test_prize_words_rejected_without_prize_context():
    turn = {**GOOD, "cited_rules": ["R01"]}
    assert any(e.startswith("off_family_term") for e in errs(turn, fired=("R01",), family="generic"))


def test_fee_word_allowed_when_note_mentioned_fee():
    turn = {**GOOD, "concern": "الملاحظة بيها رسوم.", "next_tactic": "", "question": "منو طلب؟", "cited_rules": ["R10"]}
    assert errs(turn, family="generic", extra={"رسوم"}) == []


def test_msa_markers_rejected():
    assert any(e.startswith("msa") for e in errs({**GOOD, "next_step": "يرجى التأكد من الرقم الرسمي."}))


def test_unknown_placeholder_and_links_rejected():
    assert any(e.startswith("placeholder") for e in errs({**GOOD, "concern": "حول لـ{account}"}))
    assert any(e.startswith("link") for e in errs({**GOOD, "next_step": "روح على www.example.com"}))


def test_option_rules():
    assert "options:count" in errs({**GOOD, "options": [{"id": "a", "label": "إي"}]})
    assert "options:final_turn_has_options" in errs(GOOD, final=True)
    assert any(e.startswith("not_arabic") for e in errs({**GOOD, "concern": "This is English text only"}))


def test_family_matching():
    assert match_family({"R10": {"categories": ["prize"]}}, None, "")["id"] == "prize_fee"
    assert match_family({"R07": {}, "R09": {}}, None, "")["id"] == "safe_account"
    assert match_family({"R11": {}}, None, "")["id"] == "refund_after_credit"
    assert match_family({"R12": {}}, None, "")["id"] == "hacked_relative"
    assert match_family({"R01": {}}, "exchange_deposit", "")["id"] == "exchange_deposit"
    assert match_family({"R01": {}, "R10": {"categories": ["deposit"]}}, None, "عربون")["id"] == "generic"
    assert match_family({"R01": {}, "R02": {}}, None, "")["id"] == "generic"
    # The user's own reason and the note's content outrank a structural call + impersonation combo.
    both = {"R07": {}, "R09": {}, "R10": {"categories": ["prize"]}}
    assert match_family(both, None, "")["id"] == "prize_fee"
    assert match_family({"R07": {}, "R09": {}}, "prize_fee", "")["id"] == "prize_fee"


def test_template_warning_has_no_invented_story():
    fired = [{"id": "R01", "points": 2}, {"id": "R02", "points": 2}]
    nudge = build_warning("nudge", fired, families_by_id()["generic"])
    assert "جائزة" not in nudge and "{recipient}" in nudge
    filled = fill_placeholders(nudge, {"recipient": "أحمد", "amount": "1 د.ع", "first_name": "سجاد"})
    assert "أحمد" in filled and "{" not in filled
    assert build_warning("pass", fired, families_by_id()["generic"]) is None


def test_invalid_llm_output_falls_back_to_template():
    fam = families_by_id()["generic"]
    req = CoachRequest(turn=1, fired=[{"id": "R01", "points": 2, "en": "x", "values": {}}], family=fam, amount_band=[])
    bad = {**GOOD, "cited_rules": ["R01"]}  # prize story with no prize facts
    res = asyncio.run(next_turn(req, FakeLLM(bad), timeout_s=1))
    assert res.source == "template" and res.errors
    res = asyncio.run(next_turn(req, None, timeout_s=1))
    assert res.source == "template" and res.payload["options"]


def test_template_turn_cites_strongest_rules():
    fired = [{"id": "R01", "points": 2, "en": "", "values": {}}, {"id": "R05", "points": 1, "en": "", "values": {}},
             {"id": "R10", "points": 2, "en": "", "values": {}}, {"id": "R13", "points": 6, "en": "", "values": {}}]
    req = CoachRequest(turn=1, fired=fired, family=families_by_id()["prize_fee"], amount_band=[])
    res = asyncio.run(next_turn(req, None, timeout_s=1))
    assert res.payload["cited_rules"] == ["R13", "R01", "R10"]
