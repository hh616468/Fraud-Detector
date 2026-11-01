"""Deterministic Iraqi warnings, built from the fired rules without waiting for the model."""

from __future__ import annotations

import re

from coach.catalogue import load_templates

PLACEHOLDERS = ("amount", "recipient", "first_name")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def ranked_facts(fired: list[dict], limit: int) -> list[str]:
    """Fact phrases for the highest-point risk rules, strongest first."""
    facts = load_templates()["facts"]
    risk = [r for r in fired if r["points"] > 0 and r["id"] in facts]
    risk.sort(key=lambda r: (-r["points"], r["id"]))
    return [facts[r["id"]] for r in risk[:limit]]


def join_facts(phrases: list[str]) -> str:
    return "، و".join(phrases)


def build_warning(tier: str, fired: list[dict], family: dict) -> str | None:
    """The first warning shown, with placeholders still in it. None at the pass tier."""
    t = load_templates()
    if tier == "nudge":
        return t["nudge"].replace("{facts}", join_facts(ranked_facts(fired, 2)))
    if tier == "coach":
        parts = [t["coach_header"], join_facts(ranked_facts(fired, 3)) + "."]
        parts += [family["concern"], family.get("next_tactic", ""), family["safe_step"]]
        return " ".join(p for p in parts if p)
    return None


def fill_placeholders(text: str, values: dict[str, str]) -> str:
    """Replace {amount}/{recipient}/{first_name}; unknown braces are left visible for review."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def format_iqd(amount: int) -> str:
    return f"{amount:,} د.ع"
