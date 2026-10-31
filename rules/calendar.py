"""Approximate Islamic calendar dates used by the hours rule and the simulator.

Dates depend on moon sighting in Iraq and can shift by a day; they are good enough for
synthetic data and for widening usual hours, not for anything date-critical.
"""

from __future__ import annotations

from datetime import date, datetime

RAMADAN = {
    2025: (date(2025, 3, 1), date(2025, 3, 30)),
    2026: (date(2026, 2, 18), date(2026, 3, 19)),
    2027: (date(2027, 2, 8), date(2027, 3, 9)),
}

EID_AL_FITR = {2025: date(2025, 3, 31), 2026: date(2026, 3, 20), 2027: date(2027, 3, 10)}
EID_AL_ADHA = {2025: date(2025, 6, 6), 2026: date(2026, 5, 27), 2027: date(2027, 5, 16)}


def in_ramadan(ts: datetime | date) -> bool:
    d = ts.date() if isinstance(ts, datetime) else ts
    span = RAMADAN.get(d.year)
    return bool(span and span[0] <= d <= span[1])


def days_to_eid(ts: datetime | date) -> int | None:
    """Days until the nearest Eid in the same year (negative = after), or None if unknown."""
    d = ts.date() if isinstance(ts, datetime) else ts
    candidates = [e for e in (EID_AL_FITR.get(d.year), EID_AL_ADHA.get(d.year)) if e]
    if not candidates:
        return None
    return min(((e - d).days for e in candidates), key=abs)
