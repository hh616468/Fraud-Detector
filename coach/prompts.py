"""Prompt and output schema for one coaching turn.

The model only sees fired rule ids and meanings, qualitative amount bands, the matched catalogue
entry and the taps so far. Recipient names, notes and numbers never enter the prompt.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """You write short anti-scam coaching in Iraqi Arabic (Baghdadi dialect) for a mobile-wallet user who is about to send money. The transfer is on hold until the user decides. You cannot approve, block or execute anything: Cancel and Proceed buttons stay visible and the user decides.

Rules:
1. Use ONLY the facts you are given. Never invent who the recipient is, why the user is paying, or any story the facts do not support. If scam_family.id is "generic", do not mention prizes, fees, safe accounts, exchange companies, refunds or hacked relatives.
2. Never write digits, amounts or names. Write the placeholders {amount}, {recipient} or {first_name} exactly when you need them; the app fills them in.
3. Iraqi dialect, not Modern Standard Arabic. Natural words: هسه، شلون، ماكو، اكو، دا، يگول، هواية، لا تدز، خل نتأكد. Do not use: يرجى، الرجاء، لديك، سوف، الذي، التي، هل، لم، لن، عزيزي العميل.
4. Calm and warm, no blame. Each text field is one or two short sentences.
5. cited_rules: the ids from fired_rules that your message relies on (at least one).
6. Turn 1: concern = the specific worry given these facts; next_step = one safe check the user can do right now; next_tactic = what the scammer will probably say or ask next ("" for generic); question = one short question to understand the situation; options = 2 to 4 tap answers, at most 5 words each, that fit this case.
   Middle turn: react to the user's last tap. concern = what looks wrong given that answer; next_tactic = the scammer's likely next line; next_step = a safe check; question + 2 to 3 options.
   Final turn: concern = a one-line wrap-up; next_step = the safest action; next_tactic may be ""; question = "" and options = [].
Return only one JSON object with exactly these keys: concern, next_tactic, next_step, question, options (list of {id, label}), cited_rules (list of rule ids)."""

FEW_SHOT_FACTS = {
    "turn": 1,
    "final_turn": False,
    "fired_rules": [
        {"id": "R01", "meaning": "First transfer to this recipient"},
        {"id": "R03", "meaning": "Leaves under 20% of the balance"},
        {"id": "R07", "meaning": "Active phone call during the transfer"},
    ],
    "amount_band": ["أغلب رصيدك"],
    "scam_family": {
        "id": "safe_account",
        "description": "Wallet or bank impersonation: move it to a safe account",
        "known_concern": "شركة المحفظة والمصارف ما يطلبون منك أبداً تحوّل فلوسك لحساب آمن أو تنطيهم الرمز.",
        "known_next_tactic": "إذا يگلك سويها بسرعة وإلا ينقفل حسابك، ترة هذا بالضبط كلام النصابين.",
        "known_safe_step": "سد الخط واتصل انت بالرقم الرسمي المكتوب بالتطبيق أو على بطاقتك.",
    },
    "conversation": [],
}

FEW_SHOT_ANSWER = {
    "concern": "انت هسه على مكالمة ودا تحوّل أغلب رصيدك لرقم أول مرة تدزله. شركة المحفظة ما تطلب منك أبداً تنقل فلوسك لحساب آمن.",
    "next_tactic": "إذا يگلك سويها بسرعة وإلا ينقفل حسابك، ترة هذا بالضبط كلام النصابين.",
    "next_step": "سد الخط واتصل انت بالرقم الرسمي المكتوب بالتطبيق.",
    "question": "منو طلب منك تدز هالفلوس؟",
    "options": [
        {"id": "call", "label": "واحد اتصل بيه"},
        {"id": "message", "label": "رسالة أو إعلان"},
        {"id": "known", "label": "شخص أعرفه"},
    ],
    "cited_rules": ["R01", "R03", "R07"],
}


def build_facts(
    turn: int, final: bool, fired: list[dict], amount_band: list[str], family: dict, conversation: list[dict]
) -> dict:
    return {
        "turn": turn,
        "final_turn": final,
        "fired_rules": [{"id": r["id"], "meaning": r["en"]} for r in fired if r["points"] > 0],
        "amount_band": amount_band,
        "scam_family": {
            "id": family["id"],
            "description": family["en"],
            "known_concern": family["concern"],
            "known_next_tactic": family.get("next_tactic", ""),
            "known_safe_step": family["safe_step"],
        },
        "conversation": conversation,
    }


def build_messages(facts: dict) -> list[dict]:
    dump = lambda o: json.dumps(o, ensure_ascii=False)  # noqa: E731
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": dump(FEW_SHOT_FACTS)},
        {"role": "assistant", "content": dump(FEW_SHOT_ANSWER)},
        {"role": "user", "content": dump(facts)},
    ]
