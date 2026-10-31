"""Scam-family catalogue and the matcher that picks one family from the fired rules."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

from rules import lexicons

CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "rules" / "scam_catalogue.json"
TEMPLATES_PATH = Path(__file__).resolve().parent / "templates_iq.yaml"


@lru_cache(maxsize=1)
def load_catalogue() -> dict:
    cat = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    cat["text_lexicons"] = {k: [lexicons.normalize(t) for t in v] for k, v in cat["text_lexicons"].items()}
    return cat


@lru_cache(maxsize=1)
def load_templates() -> dict:
    return yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))


def families_by_id() -> dict[str, dict]:
    cat = load_catalogue()
    out = {f["id"]: f for f in cat["families"]}
    out["generic"] = cat["generic"]
    return out


def _token_holds(token: str, fired: dict[str, dict], reason: str | None, note: str) -> bool:
    if token.startswith("reason:"):
        return reason == token.split(":", 1)[1]
    if token.startswith("text:"):
        terms = load_catalogue()["text_lexicons"][token.split(":", 1)[1]]
        return bool(lexicons.find_terms(note, terms))
    if ":" in token:  # e.g. R10:prize -> R10 fired with that category
        rule_id, category = token.split(":", 1)
        return rule_id in fired and category in fired[rule_id].get("categories", [])
    return token in fired


# Evidence weights: what the user says beats what the text shows, which beats structural combos
# (a call plus an impersonating name fits several scams; "prize" in the note fits one).
CONTENT_RULES = {"R11", "R12"}


def _token_weight(token: str) -> int:
    if token.startswith("reason:"):
        return 100
    if token.startswith("text:") or ":" in token or token in CONTENT_RULES:
        return 10
    return 1


def match_family(fired_values: dict[str, dict], reason: str | None, note: str) -> dict:
    """Pick the family with the strongest matching trigger; generic when none holds.

    `fired_values` maps each fired rule id to its supporting values.
    """
    best, best_weight = None, 0
    for fam in load_catalogue()["families"]:
        for alt in fam["triggers"]:
            weight = sum(_token_weight(t) for t in alt)
            if weight > best_weight and all(_token_holds(t, fired_values, reason, note) for t in alt):
                best, best_weight = fam, weight
    return best or load_catalogue()["generic"]
