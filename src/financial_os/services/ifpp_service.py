"""Load DB state and run IFPP engine."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings, Profile, ScheduledItem
from financial_os.engine.ifpp import (
    CardView,
    CashAccountView,
    IfppResult,
    compute_ifpp,
)
from financial_os.engine.schedule_expand import expand_scheduled
from financial_os.services.payoff import plan_card_payoff, plan_to_dict

IfppScope = Literal["entity", "group"]


def _active_accounts(session: Session, profile_id: int | None, scope: IfppScope):
    q = session.query(Account).filter(Account.archived_at.is_(None))
    if scope == "entity" and profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    return q.all()


def _resolve_scope_and_profile(
    session: Session,
    *,
    profile_id: int | None,
    scope: str | None,
) -> tuple[IfppScope, int | None, str]:
    """Return (scope, profile_id, note). Default scope is entity (silo)."""
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    raw = (scope or getattr(settings, "ifpp_scope", None) or "entity").lower()
    if raw not in ("entity", "group"):
        raw = "entity"
    sc: IfppScope = "group" if raw == "group" else "entity"

    profiles = session.query(Profile).order_by(Profile.id).all()
    note = ""

    if sc == "group":
        return sc, None, "group: all non-archived accounts"

    # entity scope
    if profile_id is None:
        # Prefer default profile, else first profile that has cash, else first profile
        default = next((p for p in profiles if p.is_default), None)
        if default:
            profile_id = default.id
            note = f"entity: default profile {default.slug}"
        elif profiles:
            profile_id = profiles[0].id
            note = f"entity: first profile {profiles[0].slug}"
        else:
            note = "entity: no profiles"
    else:
        p = session.get(Profile, profile_id)
        note = f"entity: profile {p.slug if p else profile_id}"

    return sc, profile_id, note


def run_ifpp(
    session: Session,
    *,
    as_of: date | None = None,
    mode: str | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
) -> IfppResult:
    as_of = as_of or date.today()
    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    mode = mode or settings.ifpp_mode
    horizon_days = settings.horizon_days or 45
    horizon_end = as_of + timedelta(days=horizon_days)

    sc, resolved_pid, scope_note = _resolve_scope_and_profile(
        session, profile_id=profile_id, scope=scope
    )
    accounts = _active_accounts(session, resolved_pid, sc)

    cash = [
        CashAccountView(
            id=a.id,
            name=a.nickname,
            balance=Decimal(a.current_balance or 0),
            is_cash_for_ifpp=bool(a.is_cash_for_ifpp),
            kind=a.kind or "checking",
        )
        for a in accounts
        if a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp
    ]

    cards = [
        CardView(
            id=a.id,
            name=a.nickname,
            balance=Decimal(a.current_balance or 0),
            credit_limit=Decimal(a.credit_limit or 0),
            available_credit=Decimal(a.available_credit or 0),
            statement_close_day=a.statement_close_day,
            payment_due_day=a.payment_due_day,
            apr=Decimal(a.apr) if a.apr is not None else None,
            promo_apr=Decimal(a.promo_apr) if a.promo_apr is not None else None,
            promo_end_date=a.promo_end_date,
            promo_balance=Decimal(a.promo_balance) if a.promo_balance is not None else None,
            min_payment=Decimal(a.min_payment) if a.min_payment is not None else None,
            rewards_score=0,
        )
        for a in accounts
        if a.kind == "credit"
    ]

    cliff_on = bool(getattr(settings, "income_cliff_enabled", False))
    cliff_factor = Decimal(str(getattr(settings, "income_cliff_factor", None) or "1"))
    if cliff_factor < 0:
        cliff_factor = Decimal("0")
    if cliff_factor > 1:
        cliff_factor = Decimal("1")

    sched_q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if sc == "entity" and resolved_pid is not None:
        sched_q = sched_q.filter(ScheduledItem.profile_id == resolved_pid)

    scheduled = []
    for s in sched_q.all():
        amt = Decimal(s.amount)
        cert = s.certainty or "fixed"
        if cliff_on and amt > 0 and cert in ("expected", "historical_avg"):
            amt = (amt * cliff_factor).quantize(Decimal("0.01"))
        scheduled.extend(
            expand_scheduled(
                item_id=s.id,
                name=s.name,
                amount=amt,
                next_date=s.next_date,
                cadence=s.cadence or "monthly",
                certainty=cert,
                as_of=as_of,
                horizon_end=horizon_end,
                end_date=getattr(s, "end_date", None),
                active=bool(s.active),
            )
        )

    # Tax vault is household-level today; only apply in group scope or single-entity home
    tax_vault = Decimal("0")
    if getattr(settings, "tax_vault_enabled", True):
        tax_vault = Decimal(getattr(settings, "tax_vault_balance", None) or 0)
        # In multi-entity silo, still apply vault once to personal/default entity only
        if sc == "entity" and resolved_pid is not None:
            default = (
                session.query(Profile)
                .filter(Profile.is_default.is_(True))
                .first()
            )
            if default and resolved_pid != default.id:
                tax_vault = Decimal("0")

    result = compute_ifpp(
        as_of=as_of,
        mode=mode,
        safety_buffer=Decimal(settings.safety_buffer if settings.safety_buffer is not None else 1000),
        cash_accounts=cash,
        cards=cards,
        schedule=scheduled,
        horizon_days=horizon_days,
        soft_util_pct=settings.utilization_warn_soft,
        hard_util_pct=settings.utilization_warn_hard,
        never_negative_scope=getattr(settings, "never_negative_scope", None) or "checking",
        tax_vault=tax_vault,
    )
    # Attach scope metadata for API
    result.details = dict(result.details or {})
    result.details["ifpp_scope"] = sc
    result.details["profile_id"] = resolved_pid
    result.details["scope_note"] = scope_note
    result.details["ifpp_cleared_only"] = bool(getattr(settings, "ifpp_cleared_only", True))
    # Pending exposure for UI (balances remain authoritative source for Spendable)
    from financial_os.db import Transaction
    from decimal import Decimal as D

    pq = session.query(Transaction).filter(
        Transaction.status == "pending",
        Transaction.is_transfer.is_(False),
    )
    if sc == "entity" and resolved_pid is not None:
        pq = pq.filter(Transaction.profile_id == resolved_pid)
    pending_rows = pq.all()
    pend_out = sum(
        (abs(D(str(t.amount))) for t in pending_rows if D(str(t.amount)) < 0),
        D("0"),
    )
    result.details["pending_count"] = len(pending_rows)
    result.details["pending_outflows_abs"] = str(pend_out.quantize(D("0.01")))
    result.details["pending_warning"] = (
        f"{len(pending_rows)} pending · ${pend_out} outflow may hit books soon"
        if pending_rows
        else None
    )
    return result


def ifpp_to_dict(result: IfppResult, session: Session | None = None) -> dict:
    cards_out = []
    card_views: dict[int, CardView] = {}
    if session is not None:
        from financial_os.db import Account

        q = session.query(Account).filter(Account.kind == "credit", Account.archived_at.is_(None))
        pid = (result.details or {}).get("profile_id")
        if (result.details or {}).get("ifpp_scope") == "entity" and pid is not None:
            q = q.filter(Account.profile_id == pid)
        for a in q.all():
            card_views[a.id] = CardView(
                id=a.id,
                name=a.nickname,
                balance=Decimal(a.current_balance or 0),
                credit_limit=Decimal(a.credit_limit or 0),
                available_credit=Decimal(a.available_credit or 0),
                statement_close_day=a.statement_close_day,
                payment_due_day=a.payment_due_day,
                apr=Decimal(a.apr) if a.apr is not None else None,
                promo_apr=Decimal(a.promo_apr) if a.promo_apr is not None else None,
                promo_end_date=a.promo_end_date,
                promo_balance=Decimal(a.promo_balance) if a.promo_balance is not None else None,
                min_payment=Decimal(a.min_payment) if a.min_payment is not None else None,
            )

    for c in result.cards:
        entry = {
            "account_id": c.account_id,
            "name": c.name,
            "safe_to_charge": str(c.safe_to_charge),
            "payoff_path": c.payoff_path,
            "risk": c.risk,
            "utilization_pct": str(c.utilization_pct),
            "warnings": c.warnings,
            "rewards_rank_eligible": c.rewards_rank_eligible,
            "payoff_plan": None,
        }
        if c.account_id in card_views:
            entry["payoff_plan"] = plan_to_dict(
                plan_card_payoff(card_views[c.account_id], as_of=result.as_of)
            )
        cards_out.append(entry)

    details = result.details or {}
    return {
        "as_of": result.as_of.isoformat(),
        "mode": result.mode,
        "cash_spendable": str(result.cash_spendable),
        "card_float_interest_free": str(result.card_float_interest_free),
        "combined_purchasing_power": str(result.combined_purchasing_power),
        "next_red_day": result.next_red_day.isoformat() if result.next_red_day else None,
        "is_red_now": bool(getattr(result, "is_red_now", False)),
        "pending_count": details.get("pending_count", 0),
        "pending_outflows_abs": details.get("pending_outflows_abs", "0"),
        "pending_warning": details.get("pending_warning"),
        "ifpp_cleared_only": details.get("ifpp_cleared_only"),
        "warnings": result.warnings,
        "details": result.details,
        "cards": cards_out,
        "ifpp_scope": details.get("ifpp_scope"),
        "profile_id": details.get("profile_id"),
    }
