"""Synthetic data invariants (offline pools) and the metric arithmetic."""

from datetime import timedelta

import pytest

from data import pools
from data.decisions import UserIndex, build_decisions
from data.personas import offline_persona, slots
from data.scripts import load_scripts
from data.simulator import Simulator, WalletBook
from eval.metrics import Confusion, precision_at_prevalence, wilson
from rules.engine import Scorecard


@pytest.fixture(scope="module")
def sim_users():
    book = WalletBook("test-book")
    sim = Simulator(7, book=book)
    personas = [offline_persona(s) for s in slots("val")]
    return [sim.simulate(p) for p in personas], book


def test_simulator_is_deterministic():
    persona = offline_persona(slots("val")[0])
    a = Simulator(7, book=WalletBook("x")).simulate(persona)
    b = Simulator(7, book=WalletBook("x")).simulate(persona)
    assert a.to_json() == b.to_json()


def test_histories_never_overdraw_and_amounts_are_round(sim_users):
    users, _ = sim_users
    for su in users:
        bal = su.start_balance
        for t in su.history:
            assert t["amount"] > 0 and t["amount"] % 250 == 0
            bal += t["amount"] if t["direction"] == "in" else -t["amount"]
            assert bal >= 0
        assert all(su.sim_start <= t["ts"] < su.sim_end for t in su.history)


def test_first_time_users_are_cold_start(sim_users):
    users, _ = sim_users
    first_time = [su for su in users if su.user["segment"] == "first_time"]
    assert first_time and all((su.sim_end - su.sim_start).days <= 70 for su in first_time)


def test_decisions_are_labelled_separately(sim_users):
    users, book = sim_users
    decisions, labels = build_decisions("val", users, load_scripts("A"), book, 7, held_out=False)
    assert len(decisions) == len(labels)
    assert sum(lab["is_scam"] for lab in labels) == 5 * len(users)
    for d in decisions:
        assert not {"is_scam", "family", "category"} & set(d)
    twins = [lab for lab in labels if lab["category"] == "matched_twin"]
    assert twins and all(lab["pair_id"] for lab in twins)


def test_scam_amounts_fit_the_balance(sim_users):
    users, book = sim_users
    decisions, _ = build_decisions("val", users, load_scripts("A"), book, 7, held_out=False)
    index = {su.user["id"]: UserIndex(su) for su in users}
    from datetime import datetime

    for d in decisions:
        ts = datetime.fromisoformat(d["ts"])
        extra = sum(t["amount"] if t["direction"] == "in" else -t["amount"] for t in d["setup"]["extra_history"])
        assert d["draft"]["amount"] <= index[d["user_id"]].balance_before(ts) + extra


def test_held_out_families_only_in_set_b():
    assert "marketplace_deposit" not in load_scripts("A")
    assert "marketplace_deposit" in load_scripts("B")
    assert set(pools.SCAM_SCRIPTS["A"]) < set(pools.SCAM_SCRIPTS["B"])


def test_engine_runs_on_generated_contexts(sim_users):
    from data.context import UserData, decision_context

    users, book = sim_users
    decisions, _ = build_decisions("val", users, load_scripts("A"), book, 7, held_out=False)
    data = {su.user["id"]: UserData(su.to_json()) for su in users}
    sc = Scorecard()
    for d in decisions[:50]:
        ctx = decision_context(d, data[d["user_id"]])
        assert all(t.ts < ctx.draft.ts for t in ctx.history)
        sc.assess(ctx)


def test_prevalence_arithmetic_matches_the_deck():
    # 0.80 recall, 2% FPR, 1% prevalence -> about 29% precision (slide 4).
    assert precision_at_prevalence(0.80, 0.02, 0.01) == pytest.approx(0.2878, abs=1e-3)


def test_confusion_and_wilson():
    c = Confusion()
    for truth, pred in [(True, True)] * 8 + [(True, False)] * 2 + [(False, True)] + [(False, False)] * 9:
        c.add(truth, pred)
    assert (c.tp, c.fn, c.fp, c.tn) == (8, 2, 1, 9)
    assert c.recall == 0.8 and c.fpr == 0.1
    lo, hi = wilson(8, 10)
    assert lo < 0.8 < hi and 0 <= lo and hi <= 1
    assert wilson(0, 0) is None


def test_contexts_exclude_the_decision_itself(sim_users):
    users, book = sim_users
    su = next(u for u in users if u.history)
    ui = UserIndex(su)
    t = su.history[len(su.history) // 2]
    assert ui.before(t["ts"]) == su.history.index(t)
    assert ui.balance_before(t["ts"] + timedelta(microseconds=1)) != ui.balance_before(t["ts"]) or t["amount"] == 0


def test_simulation_is_identical_across_processes():
    """Set iteration order changes with PYTHONHASHSEED; the data must not."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = ("import hashlib, json; from data.personas import offline_persona, slots; "
            "from data.simulator import Simulator, WalletBook; "
            "u = Simulator(7, book=WalletBook('x')).simulate(offline_persona(slots('val')[1])); "
            "print(hashlib.sha256(json.dumps(u.to_json(), ensure_ascii=False).encode()).hexdigest())")
    root = Path(__file__).resolve().parents[1]
    digests = {
        subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed}).stdout.strip()
        for seed in ("1", "2")
    }
    assert len(digests) == 1
