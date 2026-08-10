"""Home Simple payload — ridiculously powerful engine, stupid-simple surface."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, Profile, ScheduledItem, Transaction
from financial_os.services.capital_desk import build_capital_desk
from financial_os.services.digest import build_digest
from financial_os.services.ifpp_service import ifpp_to_dict, run_ifpp
from financial_os.services.fee_brief import build_fee_brief
from financial_os.services.import_brief import build_import_brief
from financial_os.services.month_close import build_month_close
from financial_os.services.promo_brief import build_promo_brief
from financial_os.services.recurring_detect import detect_recurring
from financial_os.services.tax_year import build_tax_year_checklist
from financial_os.services.wealth_basics import build_wealth_tips

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def build_home_simple(
    session: Session,
    *,
    profile_id: int | None = None,
    scope: str | None = None,
    mode: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    ifpp = run_ifpp(session, as_of=as_of, mode=mode, profile_id=profile_id, scope=scope)
    ifpp_d = ifpp_to_dict(ifpp, session=session)
    dig = build_digest(session, as_of=as_of, profile_id=profile_id, scope=scope)
    desk = build_capital_desk(session, as_of=as_of, profile_id=profile_id, scope=scope)

    cash = _d(ifpp.cash_spendable)
    is_red = bool(ifpp.is_red_now)
    alerts = dig.get("alerts") or []
    critical = [a for a in alerts if a.get("level") in ("critical", "warn")]

    # Status: danger | watch | safe
    if is_red:
        status = "danger"
        status_label = "Danger now"
    elif ifpp.next_red_day and ifpp.next_red_day <= as_of + timedelta(days=14):
        status = "watch"
        status_label = "Watch"
    elif critical:
        status = "watch"
        status_label = "Watch"
    else:
        status = "safe"
        status_label = "Safe"

    has_critical_fiscal = is_red or any(
        a.get("code") in ("red_now", "red_day", "neg_check", "fee_detected")
        and a.get("level") == "critical"
        for a in alerts
    )

    wealth = build_wealth_tips(
        session,
        cash_spendable=cash,
        is_red_now=is_red,
        has_critical_fiscal=has_critical_fiscal,
    )

    # Merge wealth into desk steps for Full mode consumers
    desk_steps = list(desk.get("steps") or [])
    rank0 = len(desk_steps) + 1
    for i, w in enumerate(wealth):
        desk_steps.append(
            {
                "rank": rank0 + i,
                "action": w["action"],
                "title": w["title"],
                "amount_hint": w["amount_hint"],
                "reason": w["reason"],
                "alternatives": w.get("alternatives") or [],
                "priority": "wealth",
                "disclaimer": w.get("disclaimer"),
            }
        )

    from financial_os.services.import_reminders import build_import_reminder

    import_rem = build_import_reminder(session, as_of=as_of)
    sc = ifpp_d.get("ifpp_scope") or "entity"
    pid = ifpp_d.get("profile_id")
    who_name = _who_name(session, pid)

    setup = _setup_flags(session, pid if sc == "entity" else None)
    ritual = _three_minute_check(
        session,
        ifpp=ifpp,
        dig=dig,
        setup=setup,
        cash=cash,
        desk=desk,
        profile_id=pid if sc == "entity" else None,
    )
    books = build_import_brief(
        session,
        profile_id=pid if sc == "entity" else None,
        as_of=as_of,
    )
    do_this = _do_this_next(
        ifpp, desk, alerts, wealth, cash, import_rem=import_rem, books=books
    )
    plain_alerts = [_plain_alert(a) for a in alerts[:3]]
    fees = build_fee_brief(
        session,
        profile_id=pid if sc == "entity" else None,
        as_of=as_of,
    )
    recurring = detect_recurring(
        session,
        profile_id=pid if sc == "entity" else None,
        as_of=as_of,
    )
    promo = build_promo_brief(
        session,
        profile_id=pid if sc == "entity" else None,
        as_of=as_of,
    )
    close = build_month_close(
        session,
        profile_id=pid if sc == "entity" else None,
        as_of=as_of,
    )
    tax_year = build_tax_year_checklist(session, as_of=as_of)
    # Enrich 3-min check from books + fees + bank health
    ritual = _enrich_ritual(ritual, books=books, fees=fees)

    return {
        "as_of": as_of.isoformat(),
        "safe_to_spend": str(cash.quantize(Decimal("0.01"))),
        "can_charge_no_interest": str(_d(ifpp.card_float_interest_free).quantize(Decimal("0.01"))),
        "total_power": str(_d(ifpp.combined_purchasing_power).quantize(Decimal("0.01"))),
        "status": status,
        "status_label": status_label,
        "next_risk_day": ifpp.next_red_day.isoformat() if ifpp.next_red_day else None,
        "is_red_now": is_red,
        "pending_warning": ifpp_d.get("pending_warning"),
        "pending_count": ifpp_d.get("pending_count") or 0,
        "pending_outflows_abs": ifpp_d.get("pending_outflows_abs") or "0",
        "who_name": who_name,
        "who_profile_id": pid,
        "money_view": "this_money" if sc == "entity" else "all_money",
        "ifpp_scope": sc,
        "do_this_next": do_this,
        "alerts": plain_alerts,
        "wealth_tips": wealth,
        "setup": setup,
        "three_minute_check": ritual,
        "books_brief": books,
        "import_reminder": import_rem,
        "fee_brief": fees,
        "recurring_suggestions": recurring,
        "promo_brief": promo,
        "month_close": close,
        "tax_year": tax_year,
        "digest_message": dig.get("message"),
        "headline": desk.get("headline"),
        "principles": [
            "Never go negative on checking",
            "No dumb fees or accidental APR",
            "Grow wealth only after you're safe",
            "Not investment advice — educational basics only",
        ],
        "language": {
            "safe_to_spend": "Safe to spend",
            "can_charge": "Can charge (no interest)",
            "total_power": "Total power",
            "this_money": "This money",
            "all_money": "All money",
            "who": "Who",
        },
    }


def _who_name(session: Session, profile_id: int | None) -> str:
    if profile_id is None:
        return "All"
    p = session.get(Profile, profile_id)
    return p.display_name if p else "Personal"


def _three_minute_check(
    session: Session,
    *,
    ifpp,
    dig: dict[str, Any],
    setup: dict[str, bool],
    cash: Decimal,
    desk: dict[str, Any],
    profile_id: int | None,
) -> dict[str, Any]:
    """Monthly/weekly open-rarely ritual — checklist for Simple Home."""
    uncat = int(dig.get("uncategorized_count") or 0)
    fee_alert = any(
        (a.get("code") == "fee_detected" or "fee" in (a.get("message") or "").lower())
        for a in (dig.get("alerts") or [])
    )
    promo_alert = any(
        a.get("code") in ("promo", "promo_balloon") or "promo" in (a.get("message") or "").lower()
        for a in (dig.get("alerts") or [])
    )
    head_action = (desk.get("headline") or {}).get("action") or ""
    if head_action in ("promo_balloon", "promo_sink"):
        promo_alert = True

    steps: list[dict[str, Any]] = [
        {
            "id": "checking_safe",
            "title": "Checking is safe",
            "done": not bool(ifpp.is_red_now) and cash > ZERO,
            "action": "rescue" if ifpp.is_red_now or cash <= ZERO else "hold",
            "detail": "Protect Safe to spend before anything else."
            if ifpp.is_red_now or cash <= ZERO
            else "No red-now risk on checking.",
        },
        {
            "id": "sort_charges",
            "title": "Sort a few charges" if uncat else "Charges sorted",
            "done": uncat == 0,
            "action": "review",
            "detail": f"{uncat} waiting in Review." if uncat else "Queue is clear.",
            "count": uncat,
        },
        {
            "id": "fees_ok",
            "title": "No fee alarms" if not fee_alert else "Review possible fees",
            "done": not fee_alert,
            "action": "fees" if fee_alert else "hold",
            "detail": "Fee-like activity spotted — confirm or ignore with eyes open."
            if fee_alert
            else "No fee flags right now.",
        },
        {
            "id": "promo_ok",
            "title": "0% promos on track" if not promo_alert else "Plan a 0% promo payoff",
            "done": not promo_alert,
            "action": "promo_sink" if promo_alert else "hold",
            "detail": "Promo clock needs a set-aside so you never pay APR by accident."
            if promo_alert
            else "No urgent promo balloons.",
        },
        {
            "id": "bills_known",
            "title": "Bills on the calendar" if setup.get("has_bill") else "Add your biggest bill",
            "done": bool(setup.get("has_bill")),
            "action": "add_bill" if not setup.get("has_bill") else "hold",
            "detail": "Rent/utilities make Safe to spend real."
            if not setup.get("has_bill")
            else "Recurring bills are scheduled.",
        },
    ]

    done_n = sum(1 for s in steps if s["done"])
    total = len(steps)
    all_done = done_n == total
    return {
        "title": "3-minute check",
        "subtitle": "Open rarely — tick these and close."
        if not all_done
        else "All clear — you can close the app.",
        "minutes": 3,
        "done_count": done_n,
        "total": total,
        "all_done": all_done,
        "progress_label": f"{done_n} of {total} done",
        "steps": steps,
    }


def _enrich_ritual(
    ritual: dict[str, Any],
    *,
    books: dict[str, Any],
    fees: dict[str, Any],
) -> dict[str, Any]:
    """Fold live-books + fee state into the 3-minute checklist."""
    steps = list(ritual.get("steps") or [])
    # Inject books≠bank honesty before sort_charges (after checking_safe) when drift present
    if books.get("primary_action") == "set_books_from_bank" and int(books.get("drift_count") or 0) > 0:
        steps.insert(
            1,
            {
                "id": "books_match_bank",
                "title": books.get("title") or "Books ≠ bank",
                "done": False,
                "action": "set_books_from_bank",
                "detail": books.get("reason") or "Set Safe to spend from bank ending bal.",
                "account_id": books.get("account_id"),
            },
        )
    # Update fees_ok from fee_brief (stronger than digest alone)
    for step in steps:
        if step.get("id") == "fees_ok" and fees.get("needs_attention"):
            step["done"] = False
            step["title"] = fees.get("title") or step["title"]
            step["detail"] = fees.get("reason") or step["detail"]
            step["action"] = "fees"
        if step.get("id") == "sort_charges" and books.get("uncategorized_count"):
            step["done"] = False
            n = books["uncategorized_count"]
            sample = (books.get("sample_uncategorized") or [""])[0]
            step["title"] = f"Sort a few charges ({n})"
            step["detail"] = sample or f"{n} waiting in Sort charges."
            step["count"] = n

    # Bank health step (Plaid reauth / stale)
    bank_done = not (
        books.get("plaid_needs_reauth") or (books.get("plaid_stale") and books.get("plaid_linked"))
    )
    if books.get("plaid_linked") or books.get("plaid_needs_reauth"):
        if books.get("plaid_needs_reauth"):
            bank_title, bank_detail, bank_action = (
                "Re-connect bank",
                "A linked bank login expired.",
                "plaid",
            )
            bank_done = False
        elif books.get("plaid_stale"):
            bank_title, bank_detail, bank_action = (
                "Refresh bank sync",
                "A linked bank has not synced in 3+ days.",
                "plaid",
            )
            bank_done = False
        else:
            bank_title, bank_detail, bank_action = (
                "Banks linked",
                "Sync looks current.",
                "hold",
            )
        # Insert after sort_charges if not already present
        if not any(s.get("id") == "bank_ok" for s in steps):
            insert_at = next(
                (i + 1 for i, s in enumerate(steps) if s.get("id") == "sort_charges"),
                2,
            )
            steps.insert(
                insert_at,
                {
                    "id": "bank_ok",
                    "title": bank_title,
                    "done": bank_done,
                    "action": bank_action,
                    "detail": bank_detail,
                },
            )
        else:
            for step in steps:
                if step.get("id") == "bank_ok":
                    step["title"] = bank_title
                    step["done"] = bank_done
                    step["action"] = bank_action
                    step["detail"] = bank_detail

    done_n = sum(1 for s in steps if s.get("done"))
    total = len(steps)
    ritual = dict(ritual)
    ritual["steps"] = steps
    ritual["done_count"] = done_n
    ritual["total"] = total
    ritual["all_done"] = done_n == total
    ritual["progress_label"] = f"{done_n} of {total} done"
    ritual["subtitle"] = (
        "All clear — you can close the app."
        if ritual["all_done"]
        else "Open rarely — tick these and close."
    )
    return ritual


def _setup_flags(session: Session, profile_id: int | None) -> dict[str, bool]:
    aq = session.query(Account).filter(Account.archived_at.is_(None))
    sq = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        aq = aq.filter(Account.profile_id == profile_id)
        sq = sq.filter(ScheduledItem.profile_id == profile_id)
    accounts = aq.all()
    has_cash = any(a.kind in ("checking", "savings", "cash") or a.is_cash_for_ifpp for a in accounts)
    has_card = any(a.kind == "credit" for a in accounts)
    has_loan = any(a.kind == "loan" for a in accounts)
    has_bill = sq.filter(ScheduledItem.amount < 0).count() > 0
    has_income = sq.filter(ScheduledItem.amount > 0).count() > 0
    return {
        "has_cash": has_cash,
        "has_card": has_card,
        "has_loan": has_loan,
        "has_bill": has_bill,
        "has_income": has_income,
        "needs_setup": not has_cash,
    }


def _plain_alert(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": a.get("message") or a.get("code") or "Alert",
        "level": a.get("level") or "info",
        "action": a.get("action") or a.get("code"),
        "code": a.get("code"),
        "params": {
            k: a[k]
            for k in ("account_id", "count", "pending_outflows_abs", "next_red_day")
            if k in a
        },
    }


def _do_this_next(
    ifpp,
    desk: dict[str, Any],
    alerts: list[dict],
    wealth: list[dict],
    cash: Decimal,
    import_rem: dict[str, Any] | None = None,
    books: dict[str, Any] | None = None,
) -> dict[str, Any]:
    head = desk.get("headline") or {}

    if ifpp.is_red_now or (ifpp.next_red_day and cash <= ZERO):
        return {
            "title": "Protect checking — you're at risk",
            "reason": head.get("reason")
            or "Checking is negative or safe-to-spend is $0. Fix this before anything else.",
            "action": "rescue",
            "button_label": "Show ways to stay safe",
            "params": {},
            "alternatives": head.get("alternatives")
            or [
                "Move money from savings",
                "Pause non-essential spending",
                "Delay optional bills if allowed",
            ],
            "priority": "fiscal",
        }

    # Books ≠ bank (honesty) — after red-now, before soft tips
    if books and books.get("primary_action") == "set_books_from_bank":
        return {
            "title": books.get("title") or "Books ≠ bank",
            "reason": books.get("reason") or "Set Safe to spend from bank ending balance.",
            "action": "set_books_from_bank",
            "button_label": books.get("button_label") or "Set Safe to spend from bank",
            "params": {"account_id": books.get("account_id")},
            "alternatives": ["Import → Set Safe to spend from bank", "Sort charges first if needed"],
            "priority": "books",
        }

    # Critical fee / promo (before Sort — fiscal first)
    for a in alerts:
        if a.get("level") == "critical" and a.get("code") == "promo":
            return {
                "title": a.get("message") or "0% promo needs a set-aside",
                "reason": "Pay off before promo ends so you never pay APR by accident.",
                "action": "promo_sink",
                "button_label": "Create monthly set-aside",
                "params": {"account_id": a.get("account_id")},
                "alternatives": ["Open Credit & debts for full plan"],
                "priority": "fiscal",
            }
        if a.get("level") in ("critical", "warn") and a.get("code") == "fee_detected":
            return {
                "title": "Stop fee leakage",
                "reason": a.get("message") or "Possible bank/card fees found.",
                "action": "fees",
                "button_label": "Review fees",
                "params": {},
                "alternatives": ["Turn on autopay for minimums"],
                "priority": "fiscal",
            }

    # Uncategorized only — one next action before soft wealth / import reminder
    if books and books.get("primary_action") == "review":
        uncat = int(books.get("uncategorized_count") or 0)
        if uncat > 0:
            return {
                "title": books.get("title") or f"{uncat} charge{'s' if uncat != 1 else ''} to sort",
                "reason": books.get("reason")
                or "Uncategorized charges make tax and reports fuzzy.",
                "action": "review",
                "button_label": books.get("button_label") or "Sort charges",
                "params": {},
                "alternatives": ["Import bank file", "Open Full books → Transactions"],
                "priority": "books",
            }

    action = head.get("action") or "hold"
    head_priority = head.get("priority") or "fiscal"
    # Soft = optional polish / personal tax nag. Business tax set-aside stays fiscal.
    soft = (
        action
        in (
            "hold",
            "park_yield",
            "respect_tax_vault",
            "min_only_cheap_debt",
            "credit_util",
        )
        or head_priority == "optional"
        or (action == "fund_tax_vault" and head_priority != "fiscal")
    )

    # Soft headline: money-in reminder (if due) before wealth tips — books honesty first
    if soft and import_rem and import_rem.get("due") and import_rem.get("title"):
        return {
            "title": import_rem["title"],
            "reason": import_rem.get("reason") or "Refresh books from a bank export.",
            "action": "import",
            "button_label": import_rem.get("button_label") or "Import file",
            "params": {
                "cadence": import_rem.get("cadence"),
                "focus": import_rem.get("focus"),
            },
            "alternatives": [
                "Full books → Import",
                "Optional: Banks (Plaid) with your own keys",
                "Change reminder cadence in Settings",
            ],
            "priority": "books",
        }

    # Soft headline: prefer wealth tip, else "open rarely" — never nag tax vault on day one
    if soft:
        if wealth:
            w = wealth[0]
            alts = list(w.get("alternatives") or [])
            if action in ("fund_tax_vault", "respect_tax_vault") and head.get("title"):
                alts = alts + [head["title"]]
            return {
                "title": w["title"],
                "reason": w["reason"],
                "action": w["action"],
                "button_label": "Got it — learn more",
                "params": {},
                "alternatives": alts,
                "priority": "wealth",
                "disclaimer": w.get("disclaimer"),
            }
        alts = list(head.get("alternatives") or [])
        if action in ("fund_tax_vault", "respect_tax_vault") and head.get("title"):
            # Keep tax as a quiet alternative, not the main CTA
            reason = "No urgent risk. Open rarely — we'll flag red days, fees, and promos."
            if action == "fund_tax_vault":
                alts = [head.get("title") or "Optional: set aside for taxes"] + alts
            return {
                "title": "You're clear — open rarely",
                "reason": reason,
                "action": "hold",
                "button_label": "Done for now",
                "params": {},
                "alternatives": alts[:5],
                "priority": "optional",
            }
        if action in ("hold", "park_yield"):
            return {
                "title": head.get("title") or "You're clear — open rarely",
                "reason": head.get("reason") or "No urgent fiscal gap.",
                "action": action if action != "park_yield" else "hold",
                "button_label": _button_for_action("hold"),
                "params": {},
                "alternatives": alts
                or [
                    "Pre-check big purchases with Can I buy?",
                    "Add bills so Safe to spend stays accurate",
                ],
                "priority": "optional",
            }

    return {
        "title": head.get("title") or "You're clear — open rarely",
        "reason": head.get("reason") or "No urgent fiscal gap.",
        "action": action,
        "button_label": _button_for_action(action),
        "params": {},
        "alternatives": head.get("alternatives") or [],
        "priority": head.get("priority") or "optional",
    }


def _button_for_action(action: str) -> str:
    return {
        "protect_checking": "Fix checking risk",
        "stop_fees": "Review fees",
        "promo_balloon": "Plan promo payoff",
        "attack_apr": "See debt plan",
        "top_up_buffer": "Build buffer",
        "park_yield": "Where to park cash",
        "hold": "Done for now",
        "rescue": "Show options",
        "fund_tax_vault": "Set aside for taxes",
        "respect_tax_vault": "Got it",
    }.get(action, "Continue")
