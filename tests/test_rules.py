"""One test (or pair) per rule and credit, plus cold-start guardrails, tiers and the override."""

from datetime import datetime, timedelta

from conftest import FAMILY, NEW, T0, make_ctx, routine_history, txn
from rules import lexicons
from rules.baselines import flag_all_new_payees
from rules.types import Contact, DeviceEvent, RecipientInfo


def fired(scorecard, ctx):
    return set(scorecard.assess(ctx).fired_ids)


def test_r01_new_recipient(scorecard):
    assert "R01" in fired(scorecard, make_ctx(history=routine_history()))
    assert "R01" not in fired(scorecard, make_ctx(recipient=FAMILY, history=routine_history()))


def test_new_recipient_alone_passes(scorecard):
    a = scorecard.assess(make_ctx(history=routine_history()))
    assert a.fired_ids == ["R01"] and a.tier == "pass"


def test_r02_unusual_amount_and_floor(scorecard):
    hist = routine_history()
    assert "R02" in fired(scorecard, make_ctx(amount=400_000, history=hist))
    assert "R02" not in fired(scorecard, make_ctx(amount=60_000, history=hist))
    # Tiny-amount users: above their P95 but under the IQD floor -> not "unusually large".
    small = tuple(txn(3 + i * 5, 5_000) for i in range(20))
    assert "R02" not in fired(scorecard, make_ctx(amount=40_000, history=small))


def test_r02_and_r05_off_during_cold_start(scorecard):
    hist = routine_history(n=5)
    a = scorecard.assess(make_ctx(amount=900_000, history=hist, ts=T0.replace(hour=3)))
    by_id = {r.id: r for r in a.results}
    assert by_id["R02"].disabled == "cold_start" and not by_id["R02"].fired
    assert by_id["R05"].disabled == "cold_start" and not by_id["R05"].fired


def test_new_recipient_plus_large_amount_nudges(scorecard):
    a = scorecard.assess(make_ctx(amount=400_000, history=routine_history()))
    assert set(a.fired_ids) == {"R01", "R02"} and a.score == 4 and a.tier == "nudge"


def test_r03_balance_drain(scorecard):
    assert "R03" in fired(scorecard, make_ctx(amount=850_000, balance=1_000_000))
    assert "R03" not in fired(scorecard, make_ctx(amount=500_000, balance=1_000_000))
    assert "R03" not in fired(scorecard, make_ctx(amount=20_000, balance=22_000))  # below the drain floor


def test_r04_burst(scorecard):
    hist = routine_history() + (txn(0, 10_000, ts=T0 - timedelta(minutes=5)), txn(0, 10_000, ts=T0 - timedelta(minutes=9)))
    assert "R04" in fired(scorecard, make_ctx(history=hist))
    assert "R04" not in fired(scorecard, make_ctx(history=routine_history()))


def test_r04_rising_amounts_to_new_payee(scorecard):
    hist = routine_history() + (txn(6, 100_000, cp=NEW), txn(3, 200_000, cp=NEW))
    assert "R04" in fired(scorecard, make_ctx(amount=400_000, history=hist))
    assert "R04" not in fired(scorecard, make_ctx(amount=150_000, history=hist))


def test_r05_unusual_hour_and_ramadan(scorecard):
    hist = routine_history()
    assert "R05" in fired(scorecard, make_ctx(history=hist, ts=T0.replace(hour=2)))
    assert "R05" not in fired(scorecard, make_ctx(history=hist, ts=T0.replace(hour=14)))
    ramadan_night = datetime(2026, 3, 1, 23, 0)
    hist_r = tuple(txn(0, 50_000, ts=ramadan_night - timedelta(days=3 + i * 5, hours=9)) for i in range(20))
    assert "R05" not in fired(scorecard, make_ctx(history=hist_r, ts=ramadan_night))


def test_r06_security_event_window(scorecard):
    recent = (DeviceEvent(ts=T0 - timedelta(hours=3), kind="sim_change"),)
    old = (DeviceEvent(ts=T0 - timedelta(hours=30), kind="sim_change"),)
    assert "R06" in fired(scorecard, make_ctx(events=recent))
    assert "R06" not in fired(scorecard, make_ctx(events=old))


def test_r07_active_call(scorecard):
    assert "R07" in fired(scorecard, make_ctx(call=True))
    assert "R07" not in fired(scorecard, make_ctx(call=False))


def test_r08_risky_recipient(scorecard):
    young = RecipientInfo(wallet=NEW, created_at=T0 - timedelta(days=2))
    reported = RecipientInfo(wallet=NEW, created_at=T0 - timedelta(days=200), reported=True)
    mule = RecipientInfo(wallet=NEW, created_at=T0 - timedelta(days=200), unrelated_senders_7d=12)
    for info in (young, reported, mule):
        assert "R08" in fired(scorecard, make_ctx(recipient_info=info))
    assert "R08" not in fired(scorecard, make_ctx())
    shop = RecipientInfo(wallet=NEW, created_at=T0 - timedelta(days=200), unrelated_senders_7d=40, is_business=True)
    assert "R08" not in fired(scorecard, make_ctx(recipient_info=shop))


def test_r09_impersonation_but_not_plain_names_or_billers(scorecard):
    assert "R09" in fired(scorecard, make_ctx(name="خدمة عملاء زين كاش"))
    assert "R09" in fired(scorecard, make_ctx(note="تحويل لقسم الحماية بالبنك المركزي"))
    assert "R09" not in fired(scorecard, make_ctx(name="زين العابدين"))
    assert "R09" not in fired(scorecard, make_ctx(name="عبد الرشيد"))
    biller = RecipientInfo(wallet=NEW, display_name="زين كاش للدفع", created_at=T0 - timedelta(days=900), is_biller=True)
    assert "R09" not in fired(scorecard, make_ctx(name="زين كاش للدفع", recipient_info=biller))
    charity = RecipientInfo(wallet=NEW, display_name="هيئة الرعاية", created_at=T0 - timedelta(days=900), is_business=True)
    assert "R09" not in fired(scorecard, make_ctx(name="هيئة الرعاية", recipient_info=charity))


def test_r10_fee_words_with_categories(scorecard):
    a = scorecard.assess(make_ctx(note="رسوم استلام الجائزة"))
    r10 = next(r for r in a.results if r.id == "R10")
    assert r10.fired and set(r10.values["categories"]) == {"fee", "prize"}
    assert "R10" not in fired(scorecard, make_ctx(note="عيدية للولد"))
    assert "R10" not in fired(scorecard, make_ctx(note="اجور المولدة"))


def test_r11_refund_after_stranger_credit(scorecard):
    stranger = "07812223333"
    credit = txn(0, 300_000, cp=stranger, direction="in", ts=T0 - timedelta(minutes=40))
    hist = routine_history() + (credit,)
    assert "R11" in fired(scorecard, make_ctx(recipient=stranger, amount=300_000, history=hist))
    assert "R11" in fired(scorecard, make_ctx(recipient=NEW, note="رجعلي الفلوس حولتها غلط", history=hist))
    assert "R11" not in fired(scorecard, make_ctx(recipient=NEW, history=hist))
    late = routine_history() + (txn(0, 300_000, cp=stranger, direction="in", ts=T0 - timedelta(hours=3)),)
    assert "R11" not in fired(scorecard, make_ctx(recipient=stranger, history=late))


def test_r12_contact_lookalike(scorecard):
    contacts = (Contact(name="حسين علي", wallet=FAMILY, saved_at=T0 - timedelta(days=300)),)
    assert "R12" in fired(scorecard, make_ctx(name="حسين علي", contacts=contacts))
    assert "R12" in fired(scorecard, make_ctx(name="علي حسين", contacts=contacts))
    assert "R12" not in fired(scorecard, make_ctx(name="مريم كاظم", contacts=contacts))
    assert "R12" not in fired(scorecard, make_ctx(recipient=FAMILY, name="حسين علي", contacts=contacts))


def test_r13_cue_reason_only_for_new_payees(scorecard):
    a = scorecard.assess(make_ctx(reason="prize_fee", history=routine_history()))
    assert "R13" in a.fired_ids and a.tier == "coach"
    assert "R13" not in fired(scorecard, make_ctx(recipient=FAMILY, reason="prize_fee", history=routine_history()))
    assert "R13" not in fired(scorecard, make_ctx(reason="family", history=routine_history()))


def test_m01_recurring_and_m02_saved_contact(scorecard):
    contacts = (Contact(name="حسين علي", wallet=FAMILY, saved_at=T0 - timedelta(days=300)),)
    a = scorecard.assess(make_ctx(recipient=FAMILY, history=routine_history(), contacts=contacts))
    assert {"M01", "M02"} <= set(a.fired_ids) and a.score == 0
    fresh = (Contact(name="جديد", wallet=NEW, saved_at=T0 - timedelta(days=3)),)
    assert "M02" not in fired(scorecard, make_ctx(contacts=fresh))


def test_override_security_event_plus_drain(scorecard):
    events = (DeviceEvent(ts=T0 - timedelta(hours=2), kind="new_device"),)
    a = scorecard.assess(make_ctx(recipient=FAMILY, amount=900_000, history=routine_history(), events=events))
    assert {"R03", "R06"} <= set(a.fired_ids)
    assert a.tier == "coach" and a.override == "O1"


def test_reason_tap_never_lowers_score(scorecard):
    base = scorecard.assess(make_ctx(amount=400_000, history=routine_history()))
    for code in ("family", "purchase", "bill", None):
        assert scorecard.assess(make_ctx(amount=400_000, history=routine_history(), reason=code)).score >= base.score


def test_features_ignore_future_transactions(scorecard):
    future = (txn(0, 50_000, cp=NEW, ts=T0 + timedelta(hours=1)),)
    assert "R01" in fired(scorecard, make_ctx(history=routine_history() + future))


def test_baseline_flags_all_new_payees():
    assert flag_all_new_payees(make_ctx(history=routine_history()))
    assert not flag_all_new_payees(make_ctx(recipient=FAMILY, history=routine_history()))


def test_arabic_normalisation():
    assert lexicons.normalize("جائـــزة!") == lexicons.normalize("جايزه") or lexicons.normalize("جائزة") == "جايزه"
    assert lexicons.normalize("إدارة") == "اداره"
    assert lexicons.name_similarity("حسين علي", "حسين علي") == 1.0


def test_assessment_is_fast(scorecard):
    a = scorecard.assess(make_ctx(history=routine_history(n=300)))
    assert a.latency_ms < 100
