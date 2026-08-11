"""What-if: project Spendable with hypothetical outflows or moved bills."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.engine.ifpp import ScheduledView
from financial_os.services.ifpp_service import ifpp_to_dict, run_ifpp


def simulate_ifpp(
    session: Session,
    *,
    extra_outflows: list[dict[str, Any]] | None = None,
    as_of: date | None = None,
    profile_id: int | None = None,
    scope: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Run baseline IFPP then re-run with extra scheduled outflows injected.

    extra_outflows: [{date, amount, name}] — amount may be negative or positive abs treated as outflow.
    """
    as_of = as_of or date.today()
    base = run_ifpp(session, as_of=as_of, mode=mode, profile_id=profile_id, scope=scope)
    base_d = ifpp_to_dict(base, session=session)

    # Inject extras by temporarily expanding via a shallow re-compute:
    # We re-use run_ifpp internals by patching scheduled list — simpler approach:
    # call engine with extras as additional ScheduledView by monkeypatching after load.
    from financial_os.services import ifpp_service as svc
    from financial_os.engine.ifpp import compute_ifpp
    from financial_os.db import AppSettings, Account, Profile, ScheduledItem
    from financial_os.engine.ifpp import CashAccountView, CardView
    from financial_os.engine.schedule_expand import expand_scheduled
    from datetime import timedelta

    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    sc, resolved_pid, scope_note = svc._resolve_scope_and_profile(
        session, profile_id=profile_id, scope=scope
    )
    accounts = svc._active_accounts(session, resolved_pid, sc)
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
        )
        for a in accounts
        if a.kind == "credit"
    ]
    horizon_days = settings.horizon_days or 45
    horizon_end = as_of + timedelta(days=horizon_days)
    sched_q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if sc == "entity" and resolved_pid is not None:
        sched_q = sched_q.filter(ScheduledItem.profile_id == resolved_pid)
    scheduled: list[ScheduledView] = []
    for s in sched_q.all():
        scheduled.extend(
            expand_scheduled(
                item_id=s.id,
                name=s.name,
                amount=Decimal(s.amount),
                next_date=s.next_date,
                cadence=s.cadence or "monthly",
                certainty=s.certainty or "fixed",
                as_of=as_of,
                horizon_end=horizon_end,
                end_date=getattr(s, "end_date", None),
                start_date=getattr(s, "start_date", None),
                active=bool(s.active),
            )
        )

    extras_applied = []
    for i, ex in enumerate(extra_outflows or []):
        raw_amt = Decimal(str(ex.get("amount", 0)))
        amt = -abs(raw_amt) if raw_amt != 0 else ZERO
        d = ex.get("date") or as_of.isoformat()
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        name = ex.get("name") or f"what-if #{i+1}"
        scheduled.append(
            ScheduledView(
                id=-(i + 1),
                name=name,
                amount=amt,
                on_date=d,
                certainty="fixed",
            )
        )
        extras_applied.append({"date": d.isoformat(), "amount": str(amt), "name": name})

    tax_vault = Decimal("0")
    if getattr(settings, "tax_vault_enabled", True):
        tax_vault = Decimal(getattr(settings, "tax_vault_balance", None) or 0)
        if sc == "entity" and resolved_pid is not None:
            default = session.query(Profile).filter(Profile.is_default.is_(True)).first()
            if default and resolved_pid != default.id:
                tax_vault = Decimal("0")

    sim = compute_ifpp(
        as_of=as_of,
        mode=mode or settings.ifpp_mode or "conservative",
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
    sim.details = dict(sim.details or {})
    sim.details["ifpp_scope"] = sc
    sim.details["profile_id"] = resolved_pid
    sim.details["scope_note"] = scope_note + " (simulate)"
    sim_d = ifpp_to_dict(sim, session=session)

    base_cash = Decimal(base_d["cash_spendable"])
    sim_cash = Decimal(sim_d["cash_spendable"])
    return {
        "as_of": as_of.isoformat(),
        "extras": extras_applied,
        "baseline": {
            "cash_spendable": base_d["cash_spendable"],
            "combined_purchasing_power": base_d["combined_purchasing_power"],
            "next_red_day": base_d["next_red_day"],
            "is_red_now": base_d.get("is_red_now"),
        },
        "simulated": {
            "cash_spendable": sim_d["cash_spendable"],
            "combined_purchasing_power": sim_d["combined_purchasing_power"],
            "next_red_day": sim_d["next_red_day"],
            "is_red_now": sim_d.get("is_red_now"),
            "warnings": sim_d.get("warnings", [])[:8],
        },
        "delta_cash_spendable": str((sim_cash - base_cash).quantize(Decimal("0.01"))),
        "message": (
            f"Spendable {base_cash} → {sim_cash} "
            f"(Δ {sim_cash - base_cash}). "
            f"Red day: {base_d['next_red_day'] or 'none'} → {sim_d['next_red_day'] or 'none'}."
        ),
    }


ZERO = Decimal("0")
