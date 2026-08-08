"""Optional demo accounts/schedule so IFPP is meaningful on first open."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, ScheduledItem


def seed_demo_if_empty(session: Session) -> bool:
    if session.query(Account).count() > 0:
        return False

    personal = session.query(Profile).filter(Profile.slug == "personal").one()
    ap = session.query(Profile).filter(Profile.slug == "ap_agency").one()
    aib = session.query(Profile).filter(Profile.slug == "aib42").one()

    today = date.today()

    session.add_all(
        [
            Account(
                profile_id=personal.id,
                kind="checking",
                nickname="Canvas",
                institution="Demo",
                current_balance=Decimal("1913.25"),
                is_cash_for_ifpp=True,
            ),
            Account(
                profile_id=personal.id,
                kind="checking",
                nickname="X Money",
                institution="Demo",
                current_balance=Decimal("1506.21"),
                is_cash_for_ifpp=True,
            ),
            Account(
                profile_id=personal.id,
                kind="credit",
                nickname="Amex",
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
                nickname="Discover 0% Promo",
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
                profile_id=ap.id,
                kind="checking",
                nickname="AP Agency Operating",
                institution="Demo",
                current_balance=Decimal("12400"),
                is_cash_for_ifpp=True,
            ),
            Account(
                profile_id=aib.id,
                kind="credit",
                nickname="AiB42 Azure Card",
                institution="Demo",
                current_balance=Decimal("420.50"),
                credit_limit=Decimal("5000"),
                available_credit=Decimal("4579.50"),
                statement_close_day=20,
                payment_due_day=15,
                apr=Decimal("0.1999"),
                is_cash_for_ifpp=False,
            ),
        ]
    )

    session.flush()
    canvas = (
        session.query(Account)
        .filter(Account.nickname == "Canvas", Account.profile_id == personal.id)
        .one()
    )
    ap_ops = (
        session.query(Account)
        .filter(Account.nickname == "AP Agency Operating")
        .one()
    )
    amex = (
        session.query(Account)
        .filter(Account.nickname == "Amex", Account.profile_id == personal.id)
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
                account_id=canvas.id,
                name="Housing",
                amount=Decimal("-1100"),
                next_date=housing_next,
                cadence="monthly",
                certainty="fixed",
                kind="expense",
            ),
            ScheduledItem(
                profile_id=personal.id,
                account_id=canvas.id,
                name="Paycheck (base)",
                amount=Decimal("1955.25"),
                next_date=today + timedelta(days=9),
                cadence="semimonthly",
                certainty="fixed",
                kind="income",
            ),
            ScheduledItem(
                profile_id=personal.id,
                account_id=amex.id,
                name="Utilities (on card)",
                amount=Decimal("-250"),
                next_date=today + timedelta(days=12),
                cadence="monthly",
                certainty="expected",
                kind="expense",
                notes="Charged to Amex — pay in full from Canvas",
            ),
            ScheduledItem(
                profile_id=ap.id,
                account_id=ap_ops.id,
                name="Agency operating expenses",
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
