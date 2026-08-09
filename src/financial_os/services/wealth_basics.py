"""Educational wealth-building basics — not a full investment planner.

Priority (after safety: never-neg, fees, high-APR, promos):
  1. Capture employer 401(k)/403(b) match (free money)
  2. High-interest emergency cash already handled by IFPP buffer
  3. IRA room (traditional/Roth) annual contribution habit
  4. 529 if child entities exist
  5. Optional long-horizon vehicles (IUL etc.) with heavy disclaimers

All copy is educational. Not investment, tax, or insurance advice.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Profile

ZERO = Decimal("0")

# Soft educational ceilings (US-oriented; user should verify current IRS limits)
_EDU_IRA_ANNUAL = Decimal("7000")  # illustrative; not legal limit tracking
_EDU_MATCH_HINT = "up to employer match"


def build_wealth_tips(
    session: Session,
    *,
    cash_spendable: Decimal,
    is_red_now: bool,
    has_critical_fiscal: bool,
) -> list[dict[str, Any]]:
    """Return ordered educational tips (priority=wealth). Empty if unsafe."""
    if is_red_now or has_critical_fiscal:
        return []
    # Need meaningful surplus after buffer already baked into spendable
    if cash_spendable < Decimal("200"):
        return []

    kids = (
        session.query(Profile)
        .filter(
            Profile.archived_at.is_(None),
            Profile.entity_type.in_(("child",)),
        )
        .count()
    )
    # also entity_type might be stored as child only

    tips: list[dict[str, Any]] = []

    tips.append(
        {
            "action": "wealth_401k_match",
            "title": "If work offers a 401(k)/403(b) match — take it",
            "amount_hint": _EDU_MATCH_HINT,
            "reason": (
                "Employer match is an instant return after you're safe on checking and high-APR debt. "
                "Contribute at least enough to get the full match. Verify plan rules with HR."
            ),
            "alternatives": [
                "If no workplace plan, skip to IRA habit below",
                "Increase contribution % next paycheck once cash buffer feels solid",
            ],
            "priority": "wealth",
            "disclaimer": "Not investment advice. Confirm match formula and vesting with your employer.",
        }
    )

    tips.append(
        {
            "action": "wealth_ira",
            "title": "Build an IRA habit (traditional or Roth)",
            "amount_hint": f"educational ballpark ~${_EDU_IRA_ANNUAL}/yr — verify current IRS limits",
            "reason": (
                "After match and cash safety: steady IRA contributions compound tax-advantaged. "
                "Choose Roth vs traditional with a tax pro; auto-transfer monthly from surplus."
            ),
            "alternatives": [
                "Backdoor Roth only if you already know you need it",
                "HSA if eligible (triple tax advantage) can rank beside IRA",
            ],
            "priority": "wealth",
            "disclaimer": "Limits change yearly. Not tax advice.",
        }
    )

    if kids > 0:
        tips.append(
            {
                "action": "wealth_529",
                "title": f"Consider a 529 for education ({kids} child entity(ies))",
                "amount_hint": "small automatic monthly → education goal",
                "reason": (
                    "You have child entity books in LedgerRing. A 529 can grow tax-advantaged for qualified education. "
                    "Only after checking safety, high-APR debt, and match/IRA basics."
                ),
                "alternatives": [
                    "UTMA/UGMA if you prefer broader use (tax/control tradeoffs)",
                    "Skip if education is already fully funded elsewhere",
                ],
                "priority": "wealth",
                "disclaimer": "State plans differ. Not investment advice.",
            }
        )

    tips.append(
        {
            "action": "wealth_iul_edu",
            "title": "Long-horizon options (e.g. IUL) — education only",
            "amount_hint": "optional · after match, IRA, cash buffer",
            "reason": (
                "Some households explore permanent life / IUL for protection + cash value. "
                "These are complex, fee-sensitive, and sales-driven. Only consider with a fiduciary "
                "after boring basics (match, IRA, emergency cash) are solid."
            ),
            "alternatives": [
                "Term life + invest the difference is often simpler",
                "Max boring tax-advantaged accounts first",
            ],
            "priority": "wealth",
            "disclaimer": (
                "Not insurance or investment advice. IULs can underperform illustrations. "
                "Get independent review before buying."
            ),
        }
    )

    return tips
