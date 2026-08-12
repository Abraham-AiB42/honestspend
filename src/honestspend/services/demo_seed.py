"""Optional demo accounts/schedule so IFPP is meaningful on first open.

Uses generic nicknames only — no private entity names. Optional demo business
and child are created via the public profile API helpers.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from honestspend.db import Account, Profile, ScheduledItem
from honestspend.services.profiles import create_profile


def seed_demo_if_empty(session: Session) -> bool:
    if session.query(Account).count() > 0:
        return False

    personal = session.query(Profile).filter(Profile.slug == "personal").one()
    biz = create_profile(
        session,
        display_name="Demo Business",
        entity_type="business",
        tax_form_primary="1120S",
    )
    child = create_profile(
        session,
        display_name="Demo Child",
        entity_type="child",
        parent_profile_id=personal.id,
    )

    today = date.today()

    session.add_all(
        [
            Account(
                profile_id=personal.id,
                kind="checking",
                nickname="Primary checking",
                institution="Demo Bank",
                current_balance=Decimal("1913.25"),
                is_cash_for_ifpp=True,
            ),
            Account(
                profile_id=personal.id,
                kind="savings",
                nickname="High-yield savings",
                institution="Demo Bank",
                current_balance=Decimal("1506.21"),
                apy=Decimal("0.045"),
                is_cash_for_ifpp=True,
            ),
            Account(
                profile_id=personal.id,
                kind="credit",
                nickname="Rewards card",
                institution="Demo",
                current_balance=Decimal("2422.91"),
                credit_limit=Decimal("8000"),
                available_credit=Decimal("5577.09"),
                statement_close_day=5,
                payment_due_day=28,
                apr=Decimal("0.2299"),
                is_cash_for_ifpp=False,
            ),
            Account(
                profile_id=personal.id,
                kind="credit",
                nickname="0% promo card",
                institution="Demo",
                current_balance=Decimal("3000"),
                credit_limit=Decimal("10000"),
                available_credit=Decimal("7000"),
                statement_close_day=12,
                payment_due_day=8,
                apr=Decimal("0.2499"),
                promo_apr=Decimal("0"),
                promo_end_date=today + timedelta(days=300),
                promo_balance=Decimal("3000"),
                min_payment=Decimal("75"),
                is_cash_for_ifpp=False,
            ),
            Account(
                profile_id=biz.id,
                kind="checking",
                nickname="Operating checking",
                institution="Demo Bank",
                current_balance=Decimal("12400"),
                is_cash_for_ifpp=True,
            ),
            Account(
                profile_id=biz.id,
                kind="credit",
                nickname="Business card",
                institution="Demo",
                current_balance=Decimal("420.50"),
                credit_limit=Decimal("5000"),
                available_credit=Decimal("4579.50"),
                statement_close_day=20,
                payment_due_day=15,
                apr=Decimal("0.1999"),
                is_cash_for_ifpp=False,
            ),
            Account(
                profile_id=child.id,
                kind="checking",
                nickname="Allowance account",
                institution="Demo",
                current_balance=Decimal("85.00"),
                is_cash_for_ifpp=True,
            ),
        ]
    )

    session.flush()
    checking = (
        session.query(Account)
        .filter(
            Account.nickname == "Primary checking",
            Account.profile_id == personal.id,
        )
        .one()
    )
    ops = (
        session.query(Account)
        .filter(Account.nickname == "Operating checking", Account.profile_id == biz.id)
        .one()
    )
    rewards = (
        session.query(Account)
        .filter(Account.nickname == "Rewards card", Account.profile_id == personal.id)
        .one()
    )

    if today.day == 1:
        housing_next = today
    else:
        y, m = today.year, today.month + 1
        if m > 12:
            m, y = 1, y + 1
        housing_next = date(y, m, 1)

    session.add_all(
        [
            ScheduledItem(
                profile_id=personal.id,
                account_id=checking.id,
                name="Housing",
                amount=Decimal("-1100"),
                next_date=housing_next,
                cadence="monthly",
                certainty="fixed",
                kind="expense",
            ),
            ScheduledItem(
                profile_id=personal.id,
                account_id=checking.id,
                name="Paycheck (base)",
                amount=Decimal("1955.25"),
                next_date=today + timedelta(days=9),
                cadence="semimonthly",
                certainty="fixed",
                kind="income",
            ),
            ScheduledItem(
                profile_id=personal.id,
                account_id=rewards.id,
                name="Utilities (on card)",
                amount=Decimal("-250"),
                next_date=today + timedelta(days=12),
                cadence="monthly",
                certainty="expected",
                kind="expense",
                notes="Charged to rewards card — pay in full from checking",
            ),
            ScheduledItem(
                profile_id=biz.id,
                account_id=ops.id,
                name="Business operating expenses",
                amount=Decimal("-800"),
                next_date=today + timedelta(days=7),
                cadence="monthly",
                certainty="expected",
                kind="expense",
            ),
        ]
    )

    session.flush()
    return True
