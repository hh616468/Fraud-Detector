"""Detection metrics with uncertainty: Wilson intervals and a user-clustered bootstrap."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    def add(self, truth: bool, pred: bool) -> None:
        if truth and pred:
            self.tp += 1
        elif truth:
            self.fn += 1
        elif pred:
            self.fp += 1
        else:
            self.tn += 1

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def fpr(self) -> float | None:
        return self.fp / (self.fp + self.tn) if self.fp + self.tn else None

    def as_dict(self) -> dict:
        return {"tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn, "precision": self.precision,
                "recall": self.recall, "fpr": self.fpr,
                "precision_ci": wilson(self.tp, self.tp + self.fp), "recall_ci": wilson(self.tp, self.tp + self.fn),
                "fpr_ci": wilson(self.fp, self.fp + self.tn)}


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def confusion(rows: list[dict], pred_key: str) -> Confusion:
    c = Confusion()
    for r in rows:
        c.add(r["is_scam"], r[pred_key])
    return c


def clustered_bootstrap(rows: list[dict], pred_key: str, n_boot: int = 1000, seed: int = 7) -> dict:
    """95% intervals resampling users (decisions of one user are correlated)."""
    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_user[r["user_id"]].append(r)
    users = list(by_user)
    rng = random.Random(seed)
    stats: dict[str, list[float]] = {"precision": [], "recall": [], "fpr": []}
    for _ in range(n_boot):
        c = Confusion()
        for u in (rng.choice(users) for _ in users):
            for r in by_user[u]:
                c.add(r["is_scam"], r[pred_key])
        for k in stats:
            v = getattr(c, k)
            if v is not None:
                stats[k].append(v)
    out = {}
    for k, vals in stats.items():
        vals.sort()
        out[k] = (vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]) if vals else None
    return out


def precision_at_prevalence(recall: float, fpr: float, prevalence: float) -> float | None:
    """Precision implied by a recall and false-positive rate at a given scam prevalence."""
    denom = recall * prevalence + fpr * (1 - prevalence)
    return recall * prevalence / denom if denom else None
