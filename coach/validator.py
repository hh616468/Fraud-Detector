"""Checks every model turn before a user sees it. Any error means the reviewed template is shown."""

from __future__ import annotations

import re

from coach.catalogue import families_by_id
from coach.templates import PLACEHOLDERS
from rules import lexicons

TEXT_FIELDS = ("concern", "next_tactic", "next_step", "question")
MAX_FIELD_CHARS = 220
MAX_OPTION_CHARS = 40
MAX_OPTION_WORDS = 6

_DIGITS = re.compile(r"[0-9٠-٩۰-۹]")
_URLISH = re.compile(r"(https?://|www\.|\.com\b|\.iq\b|@|wa\.me|t\.me)", re.IGNORECASE)
_BRACES = re.compile(r"\{([^{}]*)\}")
_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓکگپچ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")

# Modern Standard Arabic markers that read as a bank notice rather than Iraqi speech.
MSA_MARKERS = [
    lexicons.normalize(w)
    for w in ["يرجى", "الرجاء", "عزيزي", "عزيزتي", "نود", "سوف", "لديك", "لديكم", "الذي", "التي",
              "الذين", "لكي", "ليس", "لن", "لم", "هل", "يجب عليك", "قم"]
]


def _all_texts(turn: dict) -> list[tuple[str, str]]:
    texts = [(f, turn.get(f) or "") for f in TEXT_FIELDS]
    texts += [(f"option:{o.get('id', '?')}", o.get("label") or "") for o in turn.get("options") or []]
    return texts


def _whole_word(norm_text: str, norm_word: str) -> bool:
    return re.search(rf"(?:^|\s){re.escape(norm_word)}(?:\s|$)", norm_text) is not None


def validate_turn(
    turn: dict, fired_ids: list[str], family_id: str, final: bool, extra_allowed: set[str] | frozenset = frozenset()
) -> list[str]:
    """Return error codes; empty means the turn may be shown.

    `extra_allowed` lists family words the fired facts themselves support (e.g. "رسوم" when the
    note mentioned a fee), so stating a verified fact is not mistaken for an invented story.
    """
    errors: list[str] = []

    for f in (*TEXT_FIELDS, "options", "cited_rules"):
        if f not in turn:
            errors.append(f"missing:{f}")
    if errors:
        return errors
    if not all(isinstance(turn[f], str) for f in TEXT_FIELDS):
        return ["type:text"]
    if not isinstance(turn["options"], list) or not all(
        isinstance(o, dict) and isinstance(o.get("id"), str) and isinstance(o.get("label"), str)
        for o in turn["options"]
    ):
        return ["type:options"]

    opts = turn["options"]
    if final:
        if opts:
            errors.append("options:final_turn_has_options")
    else:
        if not 2 <= len(opts) <= 4:
            errors.append("options:count")
        if not turn["question"].strip():
            errors.append("question:empty")
    if len({o["id"] for o in opts}) != len(opts):
        errors.append("options:duplicate_id")
    if not turn["concern"].strip():
        errors.append("concern:empty")

    cited = turn["cited_rules"]
    if not isinstance(cited, list) or not cited:
        errors.append("cited_rules:empty")
    elif not set(cited) <= set(fired_ids):
        errors.append("cited_rules:not_fired")

    families = families_by_id()
    allowed = {lexicons.normalize(t) for t in families.get(family_id, families["generic"])["terms"]}
    allowed |= {lexicons.normalize(t) for t in extra_allowed}
    forbidden = {lexicons.normalize(t) for fam in families.values() for t in fam["terms"]} - allowed

    for name, text in _all_texts(turn):
        if not text:
            continue
        limit = MAX_OPTION_CHARS if name.startswith("option:") else MAX_FIELD_CHARS
        if len(text) > limit:
            errors.append(f"length:{name}")
        if name.startswith("option:") and len(text.split()) > MAX_OPTION_WORDS:
            errors.append(f"words:{name}")
        if _DIGITS.search(text):
            errors.append(f"digits:{name}")
        if _URLISH.search(text):
            errors.append(f"link:{name}")
        for ph in _BRACES.findall(text):
            if ph not in PLACEHOLDERS:
                errors.append(f"placeholder:{name}:{ph}")
        stripped = _BRACES.sub("", text)
        arabic, latin = len(_ARABIC_LETTER.findall(stripped)), len(_LATIN_LETTER.findall(stripped))
        if arabic + latin and arabic / (arabic + latin) < 0.85:
            errors.append(f"not_arabic:{name}")
        norm = lexicons.normalize(stripped)
        for term in forbidden:
            if term and term in norm:
                errors.append(f"off_family_term:{name}:{term}")
        for marker in MSA_MARKERS:
            if _whole_word(norm, marker):
                errors.append(f"msa:{name}:{marker}")
    return errors
