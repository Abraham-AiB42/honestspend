"""First-run product setup — fully in-app, no spreadsheet required."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings, Profile, ScheduledItem


@dataclass
class OnboardingStatus:
    complete: bool
    has_cash_account: bool
    has_credit_account: bool
    has_recurring: bool
    account_count: int
    product_name: str


def get_onboarding_status(session: Session) -> OnboardingStatus:
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    accounts = session.query(Account).all()
    cash = any(a.is_cash_for_ifpp or a.kind in ("checking", "savings", "cash") for a in accounts)
    credit = any(a.kind == "credit" for a in accounts)
    recurring = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True)).count() > 0
    return OnboardingStatus(
        complete=bool(getattr(settings, "onboarding_complete", False)),
        has_cash_account=cash,
        has_credit_account=credit,
        has_recurring=recurring,
        account_count=len(accounts),
        product_name=getattr(settings, "product_name", None) or "HonestSpend",
    )


def complete_onboarding(session: Session) -> AppSettings:
    settings = session.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        session.add(settings)
    settings.onboarding_complete = True
    # Keep smart wizard in sync
    if hasattr(settings, "setup_phase"):
        settings.setup_phase = "done"
    session.flush()
    try:
        from financial_os.services.backup import create_backup

        create_backup(as_zip=True, note="post-setup-skip")
    except Exception:
        pass
    return settings


def apply_quick_setup(
    session: Session,
    *,
    profile_slug: str = "personal",
    cash_name: str = "Primary checking",
    cash_balance: Decimal = Decimal("0"),
    cash_institution: str | None = None,
    card_name: str | None = None,
    card_balance: Decimal = Decimal("0"),
    card_limit: Decimal | None = None,
    card_due_day: int | None = None,
    card_close_day: int | None = None,
    card_promo_apr: Decimal | None = None,
    card_promo_end: str | None = None,
    safety_buffer: Decimal = Decimal("1000"),
    ifpp_mode: str = "conservative",
    complete_setup: bool = True,
) -> dict[str, Any]:
    """Create starter cash (+ optional card). By default marks onboarding done.

    complete_setup=False leaves the wizard open at power_menu (2-min primary + optional depth).
    """
    profile = session.query(Profile).filter(Profile.slug == profile_slug).one()
    settings = session.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        session.add(settings)
    settings.safety_buffer = safety_buffer
    settings.ifpp_mode = ifpp_mode
    settings.never_negative_scope = "checking"
    settings.opportunity_cost_aware = True

    cash = Account(
        profile_id=profile.id,
        kind="checking",
        nickname=cash_name,
        institution=cash_institution,
        current_balance=cash_balance,
        is_cash_for_ifpp=True,
        include_in_net_worth=True,
    )
    session.add(cash)
    created: dict[str, Any] = {"cash_account": cash_name}

    if card_name:
        limit = card_limit or Decimal("0")
        bal = card_balance or Decimal("0")
        avail = max(Decimal("0"), limit - bal) if limit else None
        from datetime import date as date_cls

        promo_end = None
        if card_promo_end:
            promo_end = date_cls.fromisoformat(card_promo_end[:10])
        # Defaults so interest-free IFPP path works day one
        due = card_due_day if card_due_day is not None else 15
        close = card_close_day if card_close_day is not None else 1
        card = Account(
            profile_id=profile.id,
            kind="credit",
            nickname=card_name,
            institution=cash_institution,
            current_balance=bal,
            credit_limit=limit if limit else None,
            available_credit=avail,
            statement_close_day=close,
            payment_due_day=due,
            promo_apr=card_promo_apr,
            promo_end_date=promo_end,
            promo_balance=bal if card_promo_apr is not None and card_promo_apr == 0 else None,
            is_cash_for_ifpp=False,
        )
        session.add(card)
        created["card_account"] = card_name

    if not getattr(settings, "setup_path", None):
        settings.setup_path = "manual"
    if complete_setup:
        settings.onboarding_complete = True
        if hasattr(settings, "setup_phase"):
            settings.setup_phase = "done"
    else:
        settings.onboarding_complete = False
        if hasattr(settings, "setup_phase"):
            settings.setup_phase = "power_menu"
    session.flush()
    created["onboarding_complete"] = bool(complete_setup)
    created["setup_phase"] = getattr(settings, "setup_phase", None)

    # First backup after setup (local non-negotiable)
    try:
        from financial_os.services.backup import create_backup

        bak = create_backup(as_zip=True, note="post-setup" if complete_setup else "post-manual-cash")
        created["backup"] = bak.get("name")
        created["backup_path"] = bak.get("path")
    except Exception as e:
        created["backup_error"] = str(e)

    return created


def apply_first_run(
    session: Session,
    *,
    profile_slug: str = "personal",
    cash_name: str = "Primary checking",
    cash_balance: Decimal = Decimal("0"),
    cash_institution: str | None = None,
    safety_buffer: Decimal = Decimal("1000"),
    ifpp_mode: str = "conservative",
    # optional card
    card_name: str | None = None,
    card_balance: Decimal = Decimal("0"),
    card_limit: Decimal | None = None,
    card_due_day: int | None = 15,
    card_promo_end: str | None = None,
    # optional first bill
    bill_name: str | None = None,
    bill_amount: Decimal | None = None,
    bill_next_date: str | None = None,
    # freeware money-in reminders
    import_reminder_cadence: str = "weekly",
    import_reminder_focus: str = "transactions",
    complete_setup: bool = True,
) -> dict[str, Any]:
    """Atomic first-run: cash + optional card + optional bill.

    complete_setup=False keeps the smart wizard open at power_menu.
    """
    from datetime import date as date_cls

    from financial_os.services.import_reminders import normalize_cadence, normalize_focus

    result = apply_quick_setup(
        session,
        profile_slug=profile_slug,
        cash_name=cash_name,
        cash_balance=cash_balance,
        cash_institution=cash_institution,
        card_name=card_name,
        card_balance=card_balance,
        card_limit=card_limit,
        card_due_day=card_due_day if card_name else None,
        card_close_day=1 if card_name else None,
        card_promo_apr=Decimal("0") if card_promo_end else None,
        card_promo_end=card_promo_end,
        safety_buffer=safety_buffer,
        ifpp_mode=ifpp_mode,
        complete_setup=complete_setup,
    )

    settings = session.get(AppSettings, 1)
    if settings:
        settings.import_reminder_cadence = normalize_cadence(import_reminder_cadence)
        settings.import_reminder_focus = normalize_focus(import_reminder_focus)
        session.flush()
        result["import_reminder_cadence"] = settings.import_reminder_cadence
        result["import_reminder_focus"] = settings.import_reminder_focus

    if bill_name and bill_amount is not None and abs(Decimal(bill_amount)) > 0:
        profile = session.query(Profile).filter(Profile.slug == profile_slug).one()
        cash = (
            session.query(Account)
            .filter(
                Account.profile_id == profile.id,
                Account.kind == "checking",
                Account.nickname == cash_name,
            )
            .order_by(Account.id.desc())
            .first()
        )
        nxt = date_cls.today()
        if bill_next_date:
            nxt = date_cls.fromisoformat(str(bill_next_date)[:10])
        amt = -abs(Decimal(bill_amount))
        session.add(
            ScheduledItem(
                profile_id=profile.id,
                account_id=cash.id if cash else None,
                name=bill_name.strip(),
                amount=amt,
                next_date=nxt,
                cadence="monthly",
                certainty="fixed",
                kind="expense",
                active=True,
                notes="Added during first-run wizard",
            )
        )
        session.flush()
        result["bill"] = bill_name
        result["bill_amount"] = str(amt)

    return result
