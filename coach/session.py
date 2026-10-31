"""One coaching turn: ask the model within the time budget, validate, or fall back to templates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from coach.catalogue import load_templates
from coach.llm import LLMClient, LLMError
from coach.prompts import build_facts, build_messages
from coach.validator import validate_turn

MAX_TURNS = 3

# Words the model may use when R10 fired with that category, because the note itself said so.
FEE_CATEGORY_WORDS = {
    "fee": ["رسوم"],
    "deposit": ["عربون", "ايداع"],
    "activation": ["تفعيل"],
    "grant": ["منحة"],
    "prize": ["جائزة"],
}


@dataclass
class CoachRequest:
    turn: int  # 1-based; the last allowed turn is MAX_TURNS
    fired: list[dict]  # rule results: id, points, en, values
    family: dict
    amount_band: list[str]
    conversation: list[dict] = field(default_factory=list)  # [{"question", "tapped"}]
    last_tap_id: str | None = None

    @property
    def final(self) -> bool:
        return self.turn >= MAX_TURNS

    @property
    def risk_ids(self) -> list[str]:
        return [r["id"] for r in self.fired if r["points"] > 0]


@dataclass
class TurnResult:
    source: str  # llm | template
    payload: dict
    latency_ms: float | None
    errors: list[str]


def amount_band(amount: int, balance: int, fired_ids: list[str]) -> list[str]:
    """Qualitative descriptions of the amount; the model never sees the number."""
    bands = []
    share = amount / balance if balance > 0 else 1.0
    if share >= 0.8:
        bands.append("أغلب رصيدك")
    elif share >= 0.5:
        bands.append("أكثر من نص رصيدك")
    if "R02" in fired_ids:
        bands.append("أكبر من تحويلاتك المعتادة")
    if not bands:
        bands.append("مبلغ مو كبير نسبةً لرصيدك")
    return bands


def extra_allowed_terms(fired: list[dict]) -> set[str]:
    words: set[str] = set()
    for r in fired:
        if r["id"] == "R10":
            for cat in r.get("values", {}).get("categories", []):
                words.update(FEE_CATEGORY_WORDS.get(cat, []))
    return words


def fallback_turn(req: CoachRequest) -> dict:
    chat = load_templates()["fallback_chat"]
    cited = req.risk_ids[:3] or ["R01"]
    if req.final:
        turn = dict(chat["final"])
    elif req.turn == 1:
        # The template warning above already states concern, tactic and safe step.
        turn = {"concern": "", "next_tactic": "", "next_step": "", **chat["turn1"]}
    else:
        turn = dict(chat["turn2"].get(req.last_tap_id or "", chat["turn2"]["self"]))
    turn["options"] = [dict(o) for o in turn.get("options", [])]
    turn["cited_rules"] = cited
    return turn


async def next_turn(req: CoachRequest, client: LLMClient | None, timeout_s: float) -> TurnResult:
    if client is None:
        return TurnResult("template", fallback_turn(req), None, ["llm:disabled"])
    facts = build_facts(req.turn, req.final, req.fired, req.amount_band, req.family, req.conversation)
    try:
        payload, latency = await asyncio.wait_for(
            client.chat_json(build_messages(facts), timeout_s=timeout_s),
            timeout=timeout_s,
        )
    except TimeoutError:
        return TurnResult("template", fallback_turn(req), None, ["llm:timeout"])
    except LLMError as exc:
        return TurnResult("template", fallback_turn(req), None, [f"llm:{exc}"])
    errors = validate_turn(payload, req.risk_ids, req.family["id"], req.final, extra_allowed_terms(req.fired))
    if errors:
        return TurnResult("template", fallback_turn(req), latency, errors)
    return TurnResult("llm", payload, latency, [])
