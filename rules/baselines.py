"""Reference detectors the scorecard is compared against."""

from __future__ import annotations

from rules.types import DraftContext


def flag_all_new_payees(ctx: DraftContext) -> bool:
    """Warn on every transfer to a recipient the user has never paid before."""
    d = ctx.draft
    return not any(
        t.direction == "out" and t.counterparty == d.recipient_wallet and t.ts < d.ts for t in ctx.history
    )


BASELINES = {"flag_all_new_payees": flag_all_new_payees}
