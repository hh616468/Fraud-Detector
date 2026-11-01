"""Arabic normalisation and the word lists behind R09, R10 and R11.

Recipient names and notes are user- or scammer-controlled text. They are only ever matched
against these lists as data; nothing here is executed or passed to the LLM.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

_CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "گ": "ك",
        "ڤ": "ف",
        "پ": "ب",
        "چ": "ج",
        "ک": "ك",
        "ی": "ي",
    }
)


def normalize(text: str) -> str:
    """Fold Arabic letter variants, strip diacritics/tatweel/punctuation, lowercase Latin."""
    if not text:
        return ""
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    text = text.translate(_CHAR_MAP).lower()
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _norm_all(terms: list[str]) -> list[str]:
    return [normalize(t) for t in terms]


# R09: names that pose as the wallet, a bank, a payment brand or the government.
# Bare "زين", "الرشيد" and "الرافدين" are absent: they are common personal and shop names.
IMPERSONATION_TERMS = _norm_all(
    [
        "زين كاش", "زين كاش للدفع", "zain cash", "zaincash", "zain-cash",
        "كي كارد", "كي كارت", "qi card", "qicard", "ماستر كارد", "فيزا كارد",
        "اسيا حوالة", "asiahawala", "اسياسيل حوالة", "فاست باي", "fastpay", "ناس والت", "nasswallet",
        "البنك المركزي", "central bank", "cbi", "مصرف الرافدين", "مصرف الرشيد",
        "المصرف العراقي للتجارة", "tbi", "مصرف", "بنك",
        "وزارة", "الحكومة", "حكومي", "هيئة", "مديرية", "رئاسة الوزراء", "الامن الوطني", "شرطة",
        "خدمة العملاء", "خدمه العملاء", "customer service", "support", "الدعم الفني", "دعم فني",
        "قسم الحماية", "قسم الامان", "ادارة المحفظة", "ادارة التطبيق", "admin", "official", "رسمي",
    ]
)

# R10: fee / prize / activation / grant / deposit words, grouped so the coach can tell
# a prize lure from an exchange-company deposit.
FEE_TERMS: dict[str, list[str]] = {
    # "هدية", "سحب" and "اجور" are left out: Eid gifts, cash withdrawals and generator fees are routine.
    "prize": _norm_all(["جائزة", "جايزة", "جوائز", "ربحت", "فزت", "فايز", "مسابقة", "prize"]),
    "fee": _norm_all(["رسوم", "رسم", "اجور استلام", "عمولة", "fee", "fees"]),
    "activation": _norm_all(["تفعيل", "تنشيط", "فتح الحساب", "توثيق", "activation"]),
    "grant": _norm_all(["منحة", "منحه", "دعم مالي", "grant"]),
    "deposit": _norm_all(["عربون", "عربونه", "ايداع", "deposit"]),
}

# R11: words asking to send money back.
REFUND_TERMS = _norm_all(
    ["رجع", "رجعلي", "ارجاع", "ارجع", "رجعها", "غلط", "بالغلط", "خطا", "حولتها غلط", "refund", "return"]
)

# Safe-account wording that also appears in notes.
SAFE_ACCOUNT_TERMS = _norm_all(["حساب امن", "حساب امان", "حماية الرصيد", "حماية حسابك", "safe account"])


def _contains_term(norm_text: str, norm_term: str) -> bool:
    if not norm_term:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", norm_term):
        return re.search(rf"(?<![a-z0-9]){re.escape(norm_term)}(?![a-z0-9])", norm_text) is not None
    # Arabic: match on word boundaries, allowing the definite article and common prefixes.
    pattern = rf"(?:^|\s)(?:ال|وال|بال|لل|و|ب|ل)?{re.escape(norm_term)}(?=\s|$|ه|ات|ها)"
    return re.search(pattern, norm_text) is not None


def find_terms(text: str, terms: list[str]) -> list[str]:
    norm = normalize(text)
    return [t for t in terms if _contains_term(norm, t)]


def find_fee_categories(text: str) -> dict[str, list[str]]:
    found = {}
    for category, terms in FEE_TERMS.items():
        hits = find_terms(text, terms)
        if hits:
            found[category] = hits
    return found


def name_similarity(a: str, b: str) -> float:
    """Similarity of two person names after normalisation, insensitive to token order."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    return max(SequenceMatcher(None, na, nb).ratio(), SequenceMatcher(None, ta, tb).ratio())
