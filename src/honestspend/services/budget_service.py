"""Period budgets: status, historical suggestions, reserve, cut offers."""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from honestspend.db import (
    Account,
    AppSettings,
    BudgetAdjustment,
    BudgetRule,
    Category,
    Profile,
    Transaction,
)
from honestspend.services.budget_periods import (
    DEFAULT_WEEK_START,
    DEFAULT_WORKDAYS,
    is_active_weekday,
    iter_active_days,
    next_workdays,
    period_window,
    week_bounds,
    month_bounds,
)

ZERO = Decimal("0")

# Long trailing windows (~3× thin defaults)
DAILY_LOOKBACK_DAYS = 90
WEEKLY_LOOKBACK_WEEKS = 24
MONTHLY_LOOKBACK_MONTHS = 18
BIWEEKLY_LOOKBACK = 12
YEARLY_LOOKBACK_YEARS = 2
HISTORY_LOOKBACK_DAYS = 800
ALL_DAYS_MASK = 0b1111111  # Mon–Sun
PERIODS = frozenset({"daily", "weekly", "biweekly", "monthly", "yearly"})
REVIEW_AMOUNT_FLOOR = Decimal("100")
ACTIVITY_MONTHS = 3
ACTIVITY_LIMIT = 40
STABLE_BILL_SPREAD = Decimal("5")
BILL_LOOKBACK_DAYS = 200

# Fixed obligations usually already in IFPP schedule as bills — do not also
# reserve remaining envelopes (avoids rent bill + rent budget double-count).
FIXED_OBLIGATION_BUDGET_GROUPS = frozenset(
    {
        "housing",
        "utilities",
        "insurance",
        "debt",
        "loan",
        "transfer",
        "income",
        "taxes",
        "tax",
    }
)


def normalize_period(period: str | None) -> str:
    p = (period or "monthly").strip().lower().replace("-", "")
    if p in ("annual", "annually", "year"):
        return "yearly"
    if p in ("fortnight", "fortnightly"):
        return "biweekly"
    if p == "biweekly":
        return "biweekly"
    if p in ("day", "perday"):
        return "daily"
    if p in ("workday", "perworkday"):
        return "daily"
    return p if p in PERIODS else "monthly"


def period_label(period: str | None, *, active_weekdays: int | None = None) -> str:
    p = normalize_period(period)
    if p == "daily":
        mask = int(active_weekdays if active_weekdays is not None else DEFAULT_WORKDAYS)
        if mask == ALL_DAYS_MASK:
            return "Per day"
        return "Per workday"
    return {
        "weekly": "Weekly",
        "biweekly": "Bi-weekly",
        "monthly": "Monthly",
        "yearly": "Annual",
    }.get(p, p.replace("_", " ").title())


def infer_budget_cadence(dates: list[date]) -> dict[str, Any]:
    """Pick daily / workday / weekly / biweekly / monthly / yearly from spend dates."""
    days = sorted({d for d in dates if d is not None})
    n = len(days)
    if n == 0:
        return {
            "period": "monthly",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "no spend history",
        }
    if n == 1:
        return {
            "period": "monthly",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "one charge in the books",
        }
    gaps = [(days[i] - days[i - 1]).days for i in range(1, n)]
    med = statistics.median(gaps)
    weekday_n = sum(1 for d in days if d.weekday() < 5)
    span = max(1, (days[-1] - days[0]).days)
    per_month = n / max(span / Decimal("30"), Decimal("1"))
    if n >= 6 and med <= 2.5 and weekday_n / n >= 0.75:
        return {
            "period": "daily",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "most charges land on workdays",
        }
    if n >= 8 and med <= 2.5:
        return {
            "period": "daily",
            "active_weekdays": ALL_DAYS_MASK,
            "reason": "charges land most days, including weekends",
        }
    if 5 <= med <= 9:
        return {
            "period": "weekly",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "about one charge a week",
        }
    if 11 <= med <= 18:
        return {
            "period": "biweekly",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "about every two weeks",
        }
    if n <= 3 and span >= 60:
        return {
            "period": "yearly",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "a few charges a year",
        }
    if per_month <= Decimal("0.25") and span >= 90:
        return {
            "period": "yearly",
            "active_weekdays": DEFAULT_WORKDAYS,
            "reason": "rare annual-style spend",
        }
    return {
        "period": "monthly",
        "active_weekdays": DEFAULT_WORKDAYS,
        "reason": "typical monthly spend",
    }


def expense_dates(
    session: Session,
    *,
    profile_id: int,
    category_id: int,
    start: date,
    end: date,
) -> list[date]:
    rows = (
        session.query(Transaction.txn_date)
        .filter(
            Transaction.profile_id == profile_id,
            Transaction.category_id == category_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
        )
        .distinct()
        .all()
    )
    return [r[0] for r in rows if r[0] is not None]


def format_books_date(d: date | None) -> str | None:
    """Jan 1, 2025 — works on Windows (no %-d)."""
    if d is None:
        return None
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def format_budget_money(v: Any) -> str:
    amt = _d(v).quantize(Decimal("0.01"))
    sign = "-" if amt < 0 else ""
    return f"{sign}${abs(amt):,.2f}"


def ceil_budget_amount(amount: Decimal | str | int | float, period: str | None) -> Decimal:
    """Never under-budget: next dollar; monthly/biweekly/yearly also up to the next $5."""
    amt = _d(amount)
    if amt <= 0:
        return ZERO
    dollar = amt.to_integral_value(rounding=ROUND_CEILING)
    if dollar < 1:
        dollar = Decimal("1")
    p = normalize_period(period)
    if p in ("monthly", "biweekly", "yearly"):
        rem = int(dollar) % 5
        if rem:
            dollar += Decimal(5 - rem)
        if dollar < 5:
            dollar = Decimal("5")
    return dollar


def build_habit_why(
    *,
    plan_amount: Decimal,
    period_label_text: str,
    cadence_reason: str,
    suggestion: dict[str, Any],
    charge_count: int,
    history_start: date | None,
    as_of: date,
    raw_average: Decimal | None = None,
) -> dict[str, Any]:
    """Plain-language math for a habit-seeded budget (shown behind the ?)."""
    stats = suggestion.get("stats") or {}
    avg = raw_average if raw_average is not None else _d(stats.get("avg"))
    med = _d(stats.get("median")) if stats.get("median") is not None else None
    window = suggestion.get("window")
    trend = suggestion.get("trend")
    from_l = format_books_date(history_start)
    to_l = format_books_date(as_of)
    reason = (cadence_reason or "").strip() or "that matches how often you spend here"
    lines: list[str] = [
        f"We set this as a {period_label_text.lower()} budget because {reason}."
    ]
    if from_l and to_l:
        lines.append(f"Looked at categorized charges from {from_l} to {to_l}.")
    if window:
        lines.append(
            f"Used the average over {window}. Days or weeks with no spend after you started still count, so the plan is not just the expensive days."
        )
    if avg > 0:
        bit = f"Average in that window: {format_budget_money(avg)}"
        if med is not None and med > 0:
            bit += f" (median {format_budget_money(med)})"
        lines.append(bit + ".")
    if avg > 0 and avg != _d(plan_amount):
        lines.append(
            f"Rounded {format_budget_money(avg)} to {format_budget_money(plan_amount)} so the plan is easy to remember."
        )
    else:
        lines.append(f"Plan amount: {format_budget_money(plan_amount)} {period_label_text.lower()}.")
    if charge_count:
        noun = "charge" if charge_count == 1 else "charges"
        lines.append(f"{charge_count} {noun} in the books for this category.")
    if trend == "up":
        lines.append("Recent spend is higher than earlier in the window.")
    elif trend == "down":
        lines.append("Recent spend is lower than earlier in the window.")
    return {
        "recommended": True,
        "title": f"How we recommended {format_budget_money(plan_amount)}",
        "summary": reason,
        "lines": lines,
        "period_label": period_label_text,
        "window": window,
        "charge_count": charge_count,
        "average": str(avg.quantize(Decimal("0.01"))) if avg else None,
        "plan": str(_d(plan_amount).quantize(Decimal("0.01"))),
    }


def parse_habit_why(notes: str | None) -> dict[str, Any] | None:
    if not notes:
        return None
    s = notes.strip()
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and data.get("lines"):
            return data
        return None
    if s.lower().startswith("seeded from history"):
        parts = [p.strip() for p in s.split("·") if p.strip()]
        return {
            "recommended": True,
            "title": "How we recommended this budget",
            "summary": s,
            "lines": parts,
        }
    return None


def why_for_rule(
    session: Session,
    rule: BudgetRule,
    *,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    """Explanation for habit-seeded rules; None for hand-added lines."""
    stored = parse_habit_why(getattr(rule, "notes", None))
    src = (rule.source or "").lower()
    if stored and stored.get("recommended"):
        return stored
    if src not in ("accepted_suggestion", "suggested"):
        return stored
    as_of = as_of or date.today()
    mask = int(rule.active_weekdays) if rule.active_weekdays is not None else DEFAULT_WORKDAYS
    week_start = int(rule.week_starts_on) if rule.week_starts_on is not None else DEFAULT_WEEK_START
    dates = expense_dates(
        session,
        profile_id=int(rule.profile_id),
        category_id=int(rule.category_id),
        start=as_of - timedelta(days=HISTORY_LOOKBACK_DAYS),
        end=as_of,
    )
    cadence = infer_budget_cadence(dates)
    first_seen = min(dates) if dates else None
    sug = suggest_for_category(
        session,
        profile_id=int(rule.profile_id),
        category_id=int(rule.category_id),
        period=rule.period,
        as_of=as_of,
        active_weekdays=mask,
        week_starts_on=week_start,
        history_start=first_seen,
    )
    return build_habit_why(
        plan_amount=_d(rule.amount),
        period_label_text=period_label(rule.period, active_weekdays=mask),
        cadence_reason=str(cadence.get("reason") or "from your spending habits"),
        suggestion=sug,
        charge_count=len(dates),
        history_start=first_seen,
        as_of=as_of,
        raw_average=_d(sug.get("suggested_amount")),
    )


def _trim_leading_empty(samples: list[Decimal]) -> list[Decimal]:
    i = 0
    while i < len(samples) and samples[i] <= 0:
        i += 1
    return samples[i:] if i < len(samples) else samples


def categorized_spend_span(
    session: Session,
    *,
    profile_id: int,
    as_of: date | None = None,
    max_days: int = HISTORY_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Oldest/newest categorized expense used for budget seeding."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=max_days)
    row = (
        session.query(func.min(Transaction.txn_date), func.max(Transaction.txn_date))
        .filter(
            Transaction.profile_id == profile_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
            Transaction.category_id.isnot(None),
        )
        .first()
    )
    oldest, newest = (row[0], row[1]) if row else (None, None)
    days = None
    if oldest and newest:
        days = (newest - oldest).days + 1
    return {
        "analyzed_from": oldest.isoformat() if oldest else None,
        "analyzed_to": newest.isoformat() if newest else as_of.isoformat(),
        "analyzed_from_label": format_books_date(oldest),
        "analyzed_to_label": format_books_date(newest or as_of),
        "lookback_days": days,
        "as_of": as_of.isoformat(),
    }


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _settings(session: Session) -> AppSettings:
    return session.get(AppSettings, 1) or AppSettings(id=1)


def _resolve_profile_id(session: Session, profile_id: int | None) -> int | None:
    if profile_id is not None:
        return profile_id
    default = session.query(Profile).filter(Profile.is_default.is_(True)).first()
    if default:
        return default.id
    first = session.query(Profile).order_by(Profile.id).first()
    return first.id if first else None


def expense_actual(
    session: Session,
    *,
    profile_id: int,
    category_id: int | None = None,
    start: date,
    end: date,
    category_ids: list[int] | None = None,
) -> Decimal:
    """Sum abs(outflows) for one or more categories in [start, end]. Excludes transfers."""
    ids: list[int] = []
    if category_ids:
        ids.extend(int(x) for x in category_ids if x)
    if category_id is not None:
        ids.append(int(category_id))
    ids = list(dict.fromkeys(i for i in ids if i > 0))
    if not ids:
        return ZERO
    cat_filter = Transaction.category_id.in_(ids) if len(ids) > 1 else Transaction.category_id == ids[0]
    q = (
        session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.profile_id == profile_id,
            cat_filter,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
        )
    )
    raw = q.scalar()
    # amount is negative for outflows → abs
    return abs(_d(raw))


def rule_member_ids(rule: BudgetRule) -> list[int]:
    """Primary category plus any extras attached to this budget line."""
    ids: list[int] = []
    try:
        primary = int(rule.category_id)
    except (TypeError, ValueError):
        primary = 0
    if primary > 0:
        ids.append(primary)
    raw = getattr(rule, "extra_category_ids", None)
    extra: list[Any] = []
    if raw:
        if isinstance(raw, list):
            extra = raw
        else:
            try:
                parsed = json.loads(raw)
                extra = parsed if isinstance(parsed, list) else []
            except (TypeError, json.JSONDecodeError):
                extra = []
    for x in extra:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in ids:
            ids.append(i)
    return ids


def set_rule_members(session: Session, rule: BudgetRule, ids: list[int]) -> list[int]:
    seen: list[int] = []
    for raw in ids:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.append(i)
    if not seen:
        return rule_member_ids(rule)
    rule.category_id = seen[0]
    extras = seen[1:]
    rule.extra_category_ids = json.dumps(extras) if extras else None
    rule.name = line_name_for_categories(session, seen)
    rule.updated_at = datetime.now()
    return seen


def line_name_for_categories(session: Session, ids: list[int]) -> str:
    names: list[str] = []
    for cid in ids:
        cat = session.get(Category, int(cid))
        names.append((cat.display_name if cat else None) or f"Category {cid}")
    return " + ".join(names) if names else "Budget"


def category_payload(session: Session, ids: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cid in ids:
        cat = session.get(Category, int(cid))
        out.append(
            {
                "id": int(cid),
                "name": (cat.display_name if cat else None) or f"Category {cid}",
            }
        )
    return out


def find_rule_for_category(
    session: Session,
    *,
    profile_id: int,
    category_id: int,
    exclude_rule_id: int | None = None,
) -> BudgetRule | None:
    cid = int(category_id)
    for rule in list_rules(session, profile_id):
        if exclude_rule_id is not None and int(rule.id) == int(exclude_rule_id):
            continue
        if cid in rule_member_ids(rule):
            return rule
    return None


def move_budget_category(
    session: Session,
    *,
    from_rule_id: int,
    category_id: int,
    to_rule_id: int | None = None,
    to_new: bool = False,
) -> dict[str, Any]:
    """Move a category from one budget line onto another, or onto its own line."""
    src = session.get(BudgetRule, int(from_rule_id))
    if not src or not src.active:
        return {"ok": False, "error": "Budget line not found"}
    cid = int(category_id)
    members = rule_member_ids(src)
    if cid not in members:
        return {"ok": False, "error": "That category is not on this budget line"}
    if to_new and len(members) == 1:
        return {"ok": True, "unchanged": True, "id": src.id, "message": "Already its own line"}

    dest: BudgetRule | None = None
    if to_rule_id:
        dest = session.get(BudgetRule, int(to_rule_id))
        if not dest or not dest.active:
            return {"ok": False, "error": "Destination budget line not found"}
        if int(dest.id) == int(src.id):
            return {"ok": True, "unchanged": True, "id": src.id}
        if int(dest.profile_id) != int(src.profile_id):
            return {"ok": False, "error": "Stay on the same book"}

    leftover = [m for m in members if m != cid]
    n = max(1, len(members))
    share = (_d(src.amount) / Decimal(n)).quantize(Decimal("0.01"))
    if leftover:
        take = share
        src.amount = max(ZERO, _d(src.amount) - take)
        set_rule_members(session, src, leftover)
    else:
        take = _d(src.amount)
        src.active = False
        src.updated_at = datetime.now()

    if dest is None:
        dest = create_rule(
            session,
            profile_id=int(src.profile_id),
            category_id=cid,
            period=src.period,
            amount=take,
            name=None,
            active_weekdays=src.active_weekdays,
            week_starts_on=src.week_starts_on,
            source="manual",
        )
        set_rule_members(session, dest, [cid])
        dest.amount = take
    else:
        dest_members = rule_member_ids(dest)
        if cid not in dest_members:
            dest_members.append(cid)
        set_rule_members(session, dest, dest_members)
        dest.amount = _d(dest.amount) + take

    session.flush()
    return {
        "ok": True,
        "moved": True,
        "category_id": cid,
        "from_id": src.id,
        "to_id": dest.id,
        "from_dropped": not bool(src.active),
        "from_amount": str(_d(src.amount).quantize(Decimal("0.01"))),
        "to_amount": str(_d(dest.amount).quantize(Decimal("0.01"))),
    }


def _months_back_start(as_of: date, months: int) -> date:
    y, m = as_of.year, as_of.month - (max(1, int(months)) - 1)
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def budget_line_activity(
    session: Session,
    rule: BudgetRule,
    *,
    as_of: date | None = None,
    months: int = ACTIVITY_MONTHS,
    limit: int = ACTIVITY_LIMIT,
) -> dict[str, Any]:
    """Recent charges that feed this line — shown when the plan is over $100."""
    as_of = as_of or date.today()
    members = rule_member_ids(rule)
    start = _months_back_start(as_of, months)
    amount = _d(rule.amount)
    show = amount >= REVIEW_AMOUNT_FLOOR
    empty = {
        "show": show,
        "months": months,
        "window_start": start.isoformat(),
        "window_end": as_of.isoformat(),
        "window_start_label": format_books_date(start),
        "window_end_label": format_books_date(as_of),
        "total_count": 0,
        "shown_count": 0,
        "total_abs": "0.00",
        "total_abs_display": format_budget_money(ZERO),
        "transactions": [],
    }
    if not show or not members:
        return empty
    q = session.query(Transaction).filter(
        Transaction.profile_id == int(rule.profile_id),
        Transaction.category_id.in_(members),
        Transaction.txn_date >= start,
        Transaction.txn_date <= as_of,
        Transaction.is_transfer.is_(False),
        Transaction.amount < 0,
        Transaction.status != "void",
    )
    total_count = int(q.count() or 0)
    total_abs = expense_actual(
        session,
        profile_id=int(rule.profile_id),
        category_ids=members,
        start=start,
        end=as_of,
    )
    rows = q.order_by(Transaction.txn_date.desc(), Transaction.id.desc()).limit(int(limit)).all()
    from honestspend.services.recurring_detect import categorize_payee_key, vendor_budget_key

    bill_stems = {
        v["key"]
        for v in classify_vendors_for_categories(
            session,
            profile_id=int(rule.profile_id),
            category_ids=members,
        )
        if v.get("kind") in ("stable_bill", "variable_bill", "already_billed")
    }
    wanted_keys = {categorize_payee_key(t.payee) for t in rows}
    payee_counts: dict[str, int] = {k: 0 for k in wanted_keys}
    if wanted_keys:
        for (payee,) in (
            session.query(Transaction.payee)
            .filter(
                Transaction.profile_id == int(rule.profile_id),
                Transaction.amount < 0,
                Transaction.status != "void",
            )
            .all()
        ):
            k = categorize_payee_key(payee)
            if k in payee_counts:
                payee_counts[k] += 1
    txns: list[dict[str, Any]] = []
    for t in rows:
        cat = session.get(Category, t.category_id) if t.category_id else None
        acct = session.get(Account, t.account_id) if t.account_id else None
        book = session.get(Profile, t.profile_id) if t.profile_id else None
        amt = _d(t.amount)
        from honestspend.services.recurring_detect import categorize_payee_key

        payee_key = categorize_payee_key(t.payee)
        same_n = int(payee_counts.get(payee_key) or 1)
        txns.append(
            {
                "id": int(t.id),
                "date": t.txn_date.isoformat() if t.txn_date else None,
                "date_label": format_books_date(t.txn_date),
                "payee": (t.payee or "").strip() or "(no payee)",
                "payee_key": payee_key,
                "same_payee_count": same_n,
                "is_bill": vendor_budget_key(t.payee) in bill_stems,
                "amount": str(amt.quantize(Decimal("0.01"))),
                "amount_display": format_budget_money(amt),
                "category_id": int(t.category_id) if t.category_id else None,
                "category_name": (cat.display_name if cat else None) or "Uncategorized",
                "profile_id": int(t.profile_id) if t.profile_id else None,
                "book_name": (book.display_name if book else None) or "Books",
                "account_name": (acct.nickname if acct else None) or "",
            }
        )
    return {
        "show": True,
        "months": months,
        "window_start": start.isoformat(),
        "window_end": as_of.isoformat(),
        "window_start_label": format_books_date(start),
        "window_end_label": format_books_date(as_of),
        "total_count": total_count,
        "shown_count": len(txns),
        "total_abs": str(total_abs.quantize(Decimal("0.01"))),
        "total_abs_display": format_budget_money(total_abs),
        "transactions": txns,
    }


def _variable_bill_amount(hits: list[Transaction], as_of: date) -> Decimal:
    """Highest ever, or last-year-this-month × recent trend — never under the high water."""
    amounts = [abs(_d(t.amount)) for t in hits if t.amount is not None]
    if not amounts:
        return ZERO
    max_ever = max(amounts)
    yoy = [
        abs(_d(t.amount))
        for t in hits
        if t.txn_date and t.txn_date.year == as_of.year - 1 and t.txn_date.month == as_of.month
    ]
    recent_cut = as_of - timedelta(days=186)
    prior_cut = as_of - timedelta(days=365)
    recent = [abs(_d(t.amount)) for t in hits if t.txn_date and t.txn_date >= recent_cut]
    prior = [
        abs(_d(t.amount))
        for t in hits
        if t.txn_date and prior_cut <= t.txn_date < recent_cut
    ]
    projected = max_ever
    if yoy:
        yoy_amt = max(yoy)
        if prior and recent:
            rec_avg = sum(recent) / len(recent)
            pri_avg = sum(prior) / len(prior)
            factor = (rec_avg / pri_avg) if pri_avg > 0 else Decimal("1")
            projected = (yoy_amt * factor).quantize(Decimal("0.01"))
        else:
            projected = yoy_amt
    return max(max_ever, projected)


def _typical_vendor_amounts(amounts: list[Decimal]) -> tuple[list[Decimal], list[Decimal]]:
    """Split a vendor's charges into the usual cluster vs a one-off spike (deposit / catch-up)."""
    if not amounts:
        return [], []
    if len(amounts) < 3:
        return list(amounts), []
    ordered = sorted(amounts)
    med = ordered[len(ordered) // 2]
    cap = med * Decimal("2.5") if med > 0 else max(amounts)
    typical = [a for a in amounts if a <= cap]
    spikes = [a for a in amounts if a > cap]
    if not typical:
        return list(amounts), []
    return typical, spikes


def _known_bill_stems(session: Session, profile_id: int) -> set[str]:
    from honestspend.db import ScheduledItem
    from honestspend.services.recurring_detect import vendor_budget_key

    stems: set[str] = set()
    for s in (
        session.query(ScheduledItem)
        .filter(ScheduledItem.profile_id == int(profile_id), ScheduledItem.active.is_(True))
        .all()
    ):
        for raw in (s.name, getattr(s, "vendor", None)):
            k = vendor_budget_key(raw)
            if k:
                stems.add(k)
    return stems


def classify_vendors_for_categories(
    session: Session,
    *,
    profile_id: int,
    category_ids: list[int],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Split category spend into stable bills, variable bills, and leftover vendors."""
    from collections import defaultdict

    from honestspend.services.recurring_detect import _guess_cadence, vendor_budget_key

    as_of = as_of or date.today()
    members = [int(c) for c in category_ids if c]
    if not members:
        return []
    start = as_of - timedelta(days=BILL_LOOKBACK_DAYS)
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.profile_id == int(profile_id),
            Transaction.category_id.in_(members),
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
            Transaction.status != "void",
        )
        .all()
    )
    by_key: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        key = vendor_budget_key(t.payee)
        if len(key) < 3:
            continue
        by_key[key].append(t)

    known = _known_bill_stems(session, profile_id)
    out: list[dict[str, Any]] = []
    six_ago = as_of - timedelta(days=186)
    for key, hits in by_key.items():
        dates = sorted({t.txn_date for t in hits if t.txn_date})
        names: dict[str, int] = defaultdict(int)
        acct_counts: dict[int, int] = defaultdict(int)
        for t in hits:
            names[(t.payee or key).strip()] += 1
            if t.account_id:
                acct_counts[int(t.account_id)] += 1
        display = max(names.items(), key=lambda x: x[1])[0] if names else key
        last6 = [t for t in hits if t.txn_date and t.txn_date >= six_ago] or hits
        amts6 = [abs(_d(t.amount)) for t in last6]
        typical, _spikes = _typical_vendor_amounts(amts6)
        cadence = _guess_cadence(dates)
        already = key in known
        bill_amt = max(typical) if typical else ZERO
        if already:
            kind = "already_billed"
        elif cadence and len(typical) >= 2:
            spread = max(typical) - min(typical)
            if spread <= STABLE_BILL_SPREAD:
                kind = "stable_bill"
                bill_amt = max(typical)
            else:
                kind = "variable_bill"
                bill_amt = _variable_bill_amount(
                    [t for t in hits if abs(_d(t.amount)) in typical or abs(_d(t.amount)) <= (max(typical) * Decimal("2.5") if typical else ZERO)],
                    as_of,
                )
                if typical:
                    bill_amt = max(bill_amt, max(typical))
        elif len(hits) <= 1:
            kind = "one_off"
        else:
            kind = "discretionary"
        last = dates[-1] if dates else as_of
        delta = (
            7
            if cadence == "weekly"
            else 14
            if cadence == "biweekly"
            else 365
            if cadence == "yearly"
            else 30
        )
        account_id = max(acct_counts.items(), key=lambda x: x[1])[0] if acct_counts else None
        out.append(
            {
                "key": key,
                "display": display[:128],
                "kind": kind,
                "cadence": cadence or "monthly",
                "amount": bill_amt,
                "occurrences": len(hits),
                "last_seen": last,
                "next_date": last + timedelta(days=delta),
                "account_id": account_id,
                "category_id": int(hits[0].category_id) if hits and hits[0].category_id else None,
            }
        )
    return out


def apply_vendor_budget_policy(
    session: Session,
    rule: BudgetRule,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Promote repeating vendors to bills; leftover envelope never uses a one-off spike."""
    from collections import defaultdict

    from honestspend.db import ScheduledItem
    from honestspend.services.recurring_detect import accept_recurring_suggestion, vendor_budget_key

    as_of = as_of or date.today()
    members = rule_member_ids(rule)
    vendors = classify_vendors_for_categories(
        session, profile_id=int(rule.profile_id), category_ids=members, as_of=as_of
    )
    bills: list[dict[str, Any]] = []
    billed_keys: set[str] = set()
    for v in vendors:
        kind = v["kind"]
        if kind == "already_billed":
            billed_keys.add(v["key"])
            bills.append(
                {
                    "name": v["display"],
                    "amount": str(_d(v["amount"]).quantize(Decimal("0.01"))),
                    "amount_display": format_budget_money(v["amount"]),
                    "cadence": v["cadence"],
                    "kind": kind,
                    "scheduled_id": None,
                }
            )
            continue
        if kind not in ("stable_bill", "variable_bill"):
            continue
        res = accept_recurring_suggestion(
            session,
            name=str(v["display"]),
            amount=v["amount"],
            cadence=str(v["cadence"] or "monthly"),
            next_date=v["next_date"],
            profile_id=int(rule.profile_id),
            account_id=v.get("account_id"),
        )
        sid = res.get("scheduled_id")
        row = session.get(ScheduledItem, sid) if sid else None
        if row:
            if v.get("category_id"):
                row.category_id = int(v["category_id"])
            row.vendor = str(v["display"])[:128]
            row.certainty = "fixed" if kind == "stable_bill" else "expected"
            row.opex_class = "fixed" if kind == "stable_bill" else "variable"
            row.notes = (
                "Promoted from budget · same amount across recent months"
                if kind == "stable_bill"
                else "Promoted from budget · variable bill (typical high, spikes ignored)"
            )
        billed_keys.add(v["key"])
        bills.append(
            {
                "name": v["display"],
                "amount": str(_d(v["amount"]).quantize(Decimal("0.01"))),
                "amount_display": format_budget_money(v["amount"]),
                "cadence": v["cadence"],
                "kind": kind,
                "scheduled_id": sid,
            }
        )

    leftover_txns: list[Transaction] = []
    if members:
        start = as_of - timedelta(days=BILL_LOOKBACK_DAYS)
        for t in (
            session.query(Transaction)
            .filter(
                Transaction.profile_id == int(rule.profile_id),
                Transaction.category_id.in_(members),
                Transaction.txn_date >= start,
                Transaction.txn_date <= as_of,
                Transaction.is_transfer.is_(False),
                Transaction.amount < 0,
                Transaction.status != "void",
            )
            .all()
        ):
            if vendor_budget_key(t.payee) in billed_keys:
                continue
            leftover_txns.append(t)

    # One-off vendors (park pass, a single deposit) do not set a monthly envelope.
    leftover_keys = {vendor_budget_key(t.payee) for t in leftover_txns}
    recurring_left = {v["key"] for v in vendors if v["kind"] == "discretionary"}
    envelope_txns = [
        t
        for t in leftover_txns
        if vendor_budget_key(t.payee) in recurring_left
        or leftover_keys and vendors and vendor_budget_key(t.payee) in leftover_keys
        and any(x["key"] == vendor_budget_key(t.payee) and x["kind"] == "discretionary" for x in vendors)
    ]

    raw = ZERO
    if envelope_txns:
        by_month: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
        for t in envelope_txns:
            if not t.txn_date:
                continue
            by_month[(t.txn_date.year, t.txn_date.month)] += abs(_d(t.amount))
        months = sorted(by_month.values())
        if months:
            med = months[len(months) // 2]
            typical_months = [m for m in months if med <= 0 or m <= med * Decimal("2.5")]
            monthly = max(typical_months) if typical_months else med
        else:
            monthly = ZERO
        p = normalize_period(rule.period)
        if p == "weekly":
            raw = monthly / Decimal("4")
        elif p == "biweekly":
            raw = monthly / Decimal("2")
        elif p == "daily":
            raw = monthly / Decimal("22")
        elif p == "yearly":
            raw = monthly * Decimal("12")
        else:
            raw = monthly

    amt = ceil_budget_amount(raw, rule.period) if envelope_txns else ZERO
    dropped = amt <= 0
    if dropped:
        rule.active = False
    else:
        rule.amount = amt
        rule.active = True
    mask = int(rule.active_weekdays) if rule.active_weekdays is not None else DEFAULT_WORKDAYS
    label = period_label(rule.period, active_weekdays=mask)
    bill_bits = [
        f"{b['name']}: {b['amount_display']} {b['cadence']} "
        + (
            "already on Recurring"
            if b["kind"] == "already_billed"
            else "fixed bill"
            if b["kind"] == "stable_bill"
            else "variable bill (typical high)"
        )
        for b in bills
    ]
    lines = list(bill_bits)
    if dropped:
        title = "These are bills, not a budget"
        lines.append(
            "No leftover envelope — repeating charges went to Recurring so Safe to spend "
            "does not reserve the same money twice. One-off charges are not a monthly budget."
        )
    elif envelope_txns:
        title = f"How we recommended {format_budget_money(amt)}"
        lines.append(
            f"Leftover (not a repeating bill) typical month {format_budget_money(raw)}, "
            f"rounded up to {format_budget_money(amt)} {label.lower()} so we never under-budget. "
            "A single huge month is ignored."
        )
    else:
        title = f"How we recommended {format_budget_money(amt)}"
    why = {
        "recommended": True,
        "title": title,
        "summary": "Bills vs leftover envelope",
        "lines": lines,
        "period_label": label,
        "plan": str(amt.quantize(Decimal("0.01"))),
    }
    rule.notes = json.dumps(why, ensure_ascii=False)
    rule.source = "accepted_suggestion"
    rule.updated_at = datetime.now()
    session.flush()
    return {
        "ok": True,
        "recalculated": True,
        "id": rule.id,
        "dropped": dropped,
        "amount": str(amt.quantize(Decimal("0.01"))),
        "amount_display": format_budget_money(amt),
        "raw_amount": str(raw.quantize(Decimal("0.01"))),
        "bills": bills,
        "why": why,
        "message": (
            f"{', '.join(b['name'] for b in bills)} "
            + ("are now bills. This budget line was removed." if dropped else "are now bills. Leftover envelope rounded up.")
            if bills
            else f"Recalculated to {format_budget_money(amt)} (rounded up)."
        ),
    }


def recalculate_rule_from_history(
    session: Session,
    rule: BudgetRule,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Rebuild this line: promote fixed vendors to bills, ceil leftover."""
    members = rule_member_ids(rule)
    if not members:
        return {"ok": False, "id": rule.id, "error": "No categories on this line"}
    return apply_vendor_budget_policy(session, rule, as_of=as_of)


def list_rules(session: Session, profile_id: int | None = None) -> list[BudgetRule]:
    pid = _resolve_profile_id(session, profile_id)
    if pid is None:
        return []
    return (
        session.query(BudgetRule)
        .filter(BudgetRule.profile_id == pid, BudgetRule.active.is_(True))
        .order_by(BudgetRule.period, BudgetRule.id)
        .all()
    )


def _active_adjustments(
    session: Session, rule_id: int, as_of: date
) -> list[BudgetAdjustment]:
    return (
        session.query(BudgetAdjustment)
        .filter(
            BudgetAdjustment.budget_rule_id == rule_id,
            BudgetAdjustment.applies_from <= as_of,
            BudgetAdjustment.applies_to >= as_of,
        )
        .all()
    )


def _apply_adjustments_to_remaining(
    remaining: Decimal,
    plan: Decimal,
    adjs: list[BudgetAdjustment],
    *,
    period: str,
    as_of: date,
    active_weekdays: int,
) -> Decimal:
    """Reduce remaining (release reserve) based on active adjustments."""
    rem = remaining
    for adj in adjs:
        kind = (adj.kind or "").lower()
        try:
            params = json.loads(adj.params_json or "{}")
        except json.JSONDecodeError:
            params = {}
        if kind == "release_remaining":
            rem = ZERO
        elif kind == "scale_remaining":
            factor = _d(params.get("factor", "0.5"))
            rem = (rem * factor).quantize(Decimal("0.01"))
        elif kind == "skip_workdays" and period == "daily":
            # skip today if today is in skip set (applies_from..to already scoped)
            n = int(params.get("workdays") or 1)
            skips = set(next_workdays(adj.applies_from, n, active_weekdays))
            if as_of in skips:
                rem = ZERO
        elif kind == "skip_workdays" and period != "daily":
            # free N * (plan per workday-ish) from weekly/monthly remaining
            n = int(params.get("workdays") or 1)
            # approximate daily share of plan for daily-like cuts on weekly
            if period == "weekly":
                per = (plan / Decimal("5")).quantize(Decimal("0.01"))
            elif period == "biweekly":
                per = (plan / Decimal("10")).quantize(Decimal("0.01"))
            elif period == "yearly":
                per = (plan / Decimal("260")).quantize(Decimal("0.01"))
            else:
                per = (plan / Decimal("22")).quantize(Decimal("0.01"))
            rem = max(ZERO, rem - per * n)
    return rem


def rule_status(
    session: Session,
    rule: BudgetRule,
    *,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    as_of = as_of or date.today()
    settings = _settings(session)
    week_start = int(
        getattr(rule, "week_starts_on", None)
        if rule.week_starts_on is not None
        else getattr(settings, "budget_week_starts_on", DEFAULT_WEEK_START) or 0
    )
    mask = int(rule.active_weekdays if rule.active_weekdays is not None else DEFAULT_WORKDAYS)
    win = period_window(
        rule.period,
        as_of,
        week_starts_on=week_start,
        active_weekdays=mask,
    )
    members = rule_member_ids(rule)
    member_cats = category_payload(session, members)
    joined = " + ".join(c["name"] for c in member_cats) or (
        session.get(Category, rule.category_id).display_name
        if session.get(Category, rule.category_id)
        else str(rule.category_id)
    )
    if win is None:
        # non-workday for daily rule
        return {
            "id": rule.id,
            "profile_id": rule.profile_id,
            "category_id": rule.category_id,
            "category_name": joined,
            "member_categories": member_cats,
            "name": rule.name or joined,
            "period": rule.period,
            "active_today": False,
            "plan": str(_d(rule.amount).quantize(Decimal("0.01"))),
            "actual": "0.00",
            "remaining": "0.00",
            "over": "0.00",
            "pct_used": 0.0,
            "window_label": "off today",
            "window_start": None,
            "window_end": None,
            "status": "inactive_day",
        }

    plan = _d(rule.amount)
    actual = expense_actual(
        session,
        profile_id=rule.profile_id,
        category_ids=members or [int(rule.category_id)],
        start=win.start,
        end=win.end,
    )
    remaining = max(ZERO, plan - actual)
    over = max(ZERO, actual - plan)
    adjs = _active_adjustments(session, rule.id, as_of)
    remaining = _apply_adjustments_to_remaining(
        remaining, plan, adjs, period=rule.period, as_of=as_of, active_weekdays=mask
    )
    pct = float((actual / plan * 100) if plan > 0 else (100 if actual > 0 else 0))
    st = "ok"
    if over > 0:
        st = "over"
    elif plan > 0 and remaining <= plan * Decimal("0.15"):
        st = "tight"
    return {
        "id": rule.id,
        "profile_id": rule.profile_id,
        "category_id": rule.category_id,
        "category_name": joined,
        "member_categories": member_cats,
        "name": rule.name or joined,
        "period": rule.period,
        "active_today": True,
        "plan": str(plan.quantize(Decimal("0.01"))),
        "actual": str(actual.quantize(Decimal("0.01"))),
        "remaining": str(remaining.quantize(Decimal("0.01"))),
        "over": str(over.quantize(Decimal("0.01"))),
        "pct_used": round(pct, 1),
        "window_label": win.label,
        "window_start": win.start.isoformat(),
        "window_end": win.end.isoformat(),
        "status": st,
        "source": rule.source,
        "active_weekdays": mask,
        "week_starts_on": week_start,
    }


def budgets_status(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    pid = _resolve_profile_id(session, profile_id)
    if pid is None:
        return {"as_of": as_of.isoformat(), "items": [], "reserve_total": "0.00"}
    items = []
    # Honesty: max remaining per category; skip fixed-obligation groups (bills already
    # in IFPP schedule) so rent isn't reserved twice.
    best_by_cat: dict[int, Decimal] = {}
    skipped_fixed = 0
    for rule in list_rules(session, pid):
        st = rule_status(session, rule, as_of=as_of)
        if st:
            items.append(st)
            if st.get("active_today"):
                cid = int(st.get("category_id") or 0)
                rem = _d(st.get("remaining"))
                if rem < ZERO:
                    rem = ZERO
                if cid <= 0:
                    continue
                cat = session.get(Category, cid)
                bg = (cat.budget_group or "").lower() if cat else ""
                if bg in FIXED_OBLIGATION_BUDGET_GROUPS:
                    skipped_fixed += 1
                    st["reserves_safe_to_spend"] = False
                    continue
                st["reserves_safe_to_spend"] = True
                prev = best_by_cat.get(cid, ZERO)
                if rem > prev:
                    best_by_cat[cid] = rem
    reserve = sum(best_by_cat.values(), ZERO)
    settings = _settings(session)
    return {
        "as_of": as_of.isoformat(),
        "profile_id": pid,
        "reserve_enabled": bool(getattr(settings, "budget_reserve_enabled", True)),
        "reserve_total": str(reserve.quantize(Decimal("0.01"))),
        "reserve_mode": "max_remaining_per_category_discretionary",
        "skipped_fixed_obligation_rules": skipped_fixed,
        "items": items,
        "by_period": {
            "daily": [i for i in items if i["period"] == "daily"],
            "weekly": [i for i in items if i["period"] == "weekly"],
            "biweekly": [i for i in items if i["period"] == "biweekly"],
            "monthly": [i for i in items if i["period"] == "monthly"],
            "yearly": [i for i in items if i["period"] == "yearly"],
        },
    }


def budget_reserve_total(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
    scope: str | None = None,
) -> Decimal:
    """Sum of max-per-category remaining envelopes.

    scope=group → sum reserves across all non-archived profiles (All money view).
    """
    settings = _settings(session)
    if not bool(getattr(settings, "budget_reserve_enabled", True)):
        return ZERO
    sc = (scope or "entity").strip().lower()
    if sc == "group":
        from honestspend.db import Profile

        total = ZERO
        q = session.query(Profile)
        if hasattr(Profile, "archived_at"):
            q = q.filter(Profile.archived_at.is_(None))
        for p in q.all():
            total += _d(
                budgets_status(session, profile_id=p.id, as_of=as_of).get("reserve_total")
            )
        return total.quantize(Decimal("0.01"))
    st = budgets_status(session, profile_id=profile_id, as_of=as_of)
    return _d(st.get("reserve_total"))


def _daily_series(
    session: Session,
    profile_id: int,
    category_id: int,
    start: date,
    end: date,
    active_weekdays: int,
) -> list[Decimal]:
    days = iter_active_days(start, end, active_weekdays)
    series: list[Decimal] = []
    for d in days:
        series.append(
            expense_actual(
                session,
                profile_id=profile_id,
                category_id=category_id,
                start=d,
                end=d,
            )
        )
    return series


def suggest_for_category(
    session: Session,
    *,
    profile_id: int,
    category_id: int,
    period: str,
    as_of: date | None = None,
    active_weekdays: int = DEFAULT_WORKDAYS,
    week_starts_on: int = DEFAULT_WEEK_START,
    history_start: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    p = normalize_period(period)
    cat = session.get(Category, category_id)
    name = cat.display_name if cat else str(category_id)
    if history_start is None:
        seen = expense_dates(
            session,
            profile_id=profile_id,
            category_id=category_id,
            start=as_of - timedelta(days=HISTORY_LOOKBACK_DAYS),
            end=as_of,
        )
        if seen:
            history_start = min(seen)

    def clip(d: date) -> date:
        if history_start and history_start > d:
            return history_start
        return d

    samples: list[Decimal] = []
    if p == "daily":
        start = clip(as_of - timedelta(days=DAILY_LOOKBACK_DAYS))
        samples = _daily_series(
            session, profile_id, category_id, start, as_of - timedelta(days=1), active_weekdays
        )
        window_desc = f"{max(1, (as_of - start).days)}d workdays"
    elif p == "weekly":
        samples = []
        cur_end = as_of - timedelta(days=1)
        for _ in range(WEEKLY_LOOKBACK_WEEKS):
            ws, we = week_bounds(cur_end, week_starts_on)
            if history_start and we < history_start:
                break
            samples.append(
                expense_actual(
                    session,
                    profile_id=profile_id,
                    category_id=category_id,
                    start=clip(ws),
                    end=we,
                )
            )
            cur_end = ws - timedelta(days=1)
        samples.reverse()
        window_desc = f"{len(samples) or WEEKLY_LOOKBACK_WEEKS} weeks"
    elif p == "biweekly":
        samples = []
        cur_end = as_of - timedelta(days=1)
        for _ in range(BIWEEKLY_LOOKBACK):
            start = cur_end - timedelta(days=13)
            if history_start and cur_end < history_start:
                break
            samples.append(
                expense_actual(
                    session,
                    profile_id=profile_id,
                    category_id=category_id,
                    start=clip(start),
                    end=cur_end,
                )
            )
            cur_end = start - timedelta(days=1)
        samples.reverse()
        window_desc = f"{len(samples) or BIWEEKLY_LOOKBACK} fortnights"
    elif p == "yearly":
        samples = []
        for i in range(YEARLY_LOOKBACK_YEARS):
            end = as_of - timedelta(days=365 * i)
            start = end - timedelta(days=364)
            if history_start and end < history_start:
                break
            samples.append(
                expense_actual(
                    session,
                    profile_id=profile_id,
                    category_id=category_id,
                    start=clip(start),
                    end=end,
                )
            )
        samples.reverse()
        window_desc = f"{len(samples) or YEARLY_LOOKBACK_YEARS} year(s)"
    else:
        samples = []
        y, m = as_of.year, as_of.month
        # previous complete months
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
        for _ in range(MONTHLY_LOOKBACK_MONTHS):
            ms, me = month_bounds(date(y, m, 15))
            if history_start and me < history_start:
                break
            samples.append(
                expense_actual(
                    session,
                    profile_id=profile_id,
                    category_id=category_id,
                    start=clip(ms),
                    end=me,
                )
            )
            if m == 1:
                y, m = y - 1, 12
            else:
                m -= 1
        samples.reverse()
        window_desc = f"{len(samples) or MONTHLY_LOOKBACK_MONTHS} months"

    samples = _trim_leading_empty(samples)
    nonzero = [s for s in samples if s > 0]
    use = samples  # include interior zeros (weeks/days they skipped after spending started)

    def _stat(xs: list[Decimal]) -> dict[str, Any]:
        if not xs:
            return {"avg": "0.00", "median": "0.00", "min": "0.00", "max": "0.00", "n": 0}
        f = [float(x) for x in xs]
        return {
            "avg": str(Decimal(str(statistics.mean(f))).quantize(Decimal("0.01"))),
            "median": str(Decimal(str(statistics.median(f))).quantize(Decimal("0.01"))),
            "min": str(min(xs).quantize(Decimal("0.01"))),
            "max": str(max(xs).quantize(Decimal("0.01"))),
            "n": len(xs),
        }

    stats = _stat(use)
    suggested = _d(stats["avg"])

    # simple trend: last third vs prior third
    trend = "flat"
    if len(use) >= 6:
        third = len(use) // 3
        recent = use[-third:]
        prior = use[-2 * third : -third]
        if prior:
            r = sum(recent) / len(recent)
            p0 = sum(prior) / len(prior)
            if p0 > 0 and r > p0 * Decimal("1.15"):
                trend = "up"
            elif p0 > 0 and r < p0 * Decimal("0.85"):
                trend = "down"

    return {
        "category_id": category_id,
        "category_name": name,
        "period": p,
        "window": window_desc,
        "suggested_amount": str(suggested.quantize(Decimal("0.01"))),
        "stats": stats,
        "nonzero_stats": _stat(nonzero) if nonzero else stats,
        "trend": trend,
        "sample_count": len(use),
    }


def suggestions(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Suggestions for existing rules + top spend categories without rules."""
    as_of = as_of or date.today()
    pid = _resolve_profile_id(session, profile_id)
    if pid is None:
        return {"items": []}
    settings = _settings(session)
    mask = int(getattr(settings, "budget_workdays", DEFAULT_WORKDAYS) or DEFAULT_WORKDAYS)
    week_start = int(getattr(settings, "budget_week_starts_on", DEFAULT_WEEK_START) or 0)

    items = []
    ruled: set[tuple[int, str]] = set()
    for rule in list_rules(session, pid):
        sug = suggest_for_category(
            session,
            profile_id=pid,
            category_id=rule.category_id,
            period=rule.period,
            as_of=as_of,
            active_weekdays=int(rule.active_weekdays or mask),
            week_starts_on=int(rule.week_starts_on if rule.week_starts_on is not None else week_start),
        )
        sug["budget_rule_id"] = rule.id
        sug["current_amount"] = str(_d(rule.amount).quantize(Decimal("0.01")))
        sug["has_rule"] = True
        items.append(sug)
        ruled.add((rule.category_id, rule.period))

    # Top spend categories without a rule — propose daily/weekly/monthly from history
    lookback_start = as_of - timedelta(days=DAILY_LOOKBACK_DAYS)
    rows = (
        session.query(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .filter(
            Transaction.profile_id == pid,
            Transaction.txn_date >= lookback_start,
            Transaction.txn_date <= as_of,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
            Transaction.category_id.isnot(None),
        )
        .group_by(Transaction.category_id)
        .order_by(func.sum(Transaction.amount).asc())  # most negative first
        .limit(12)
        .all()
    )
    for cat_id, raw_sum in rows:
        if cat_id is None:
            continue
        cid = int(cat_id)
        # Prefer weekly suggestion for new categories (most common Excel habit after daily food)
        for period in ("daily", "weekly", "monthly"):
            if (cid, period) in ruled:
                continue
            sug = suggest_for_category(
                session,
                profile_id=pid,
                category_id=cid,
                period=period,
                as_of=as_of,
                active_weekdays=mask,
                week_starts_on=week_start,
            )
            if _d(sug.get("suggested_amount")) <= 0:
                continue
            sug["budget_rule_id"] = None
            sug["current_amount"] = None
            sug["has_rule"] = False
            items.append(sug)
            ruled.add((cid, period))
            break  # one period suggestion per unbudgeted category

    return {"as_of": as_of.isoformat(), "profile_id": pid, "items": items}


def _preferred_period_for_category(cat: Category | None) -> str:
    """Map COA budget_group / name to daily|weekly|monthly (Excel habits)."""
    if cat is None:
        return "weekly"
    bg = (cat.budget_group or "").lower()
    name = (cat.display_name or "").lower()
    code = (cat.code or "").lower()
    if bg in ("food",) or "food" in name or "groc" in name or "dining" in name or "lunch" in name:
        return "daily"
    if bg in ("transport",) or "gas" in name or "fuel" in name or "gas" in code:
        return "weekly"
    if bg in ("housing", "utilities", "insurance") or any(
        x in name for x in ("rent", "mortgage", "util", "electric", "water", "internet", "cell", "insur")
    ):
        return "monthly"
    if bg in ("debt", "income", "transfer", "owner"):
        return "monthly"
    return "weekly"


def seed_from_history(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
    max_rules: int = 10,
    min_suggested: Decimal = Decimal("5"),
    only_if_empty: bool = False,
) -> dict[str, Any]:
    """Create budget rules from top historical spend using long trailing averages.

    Skips categories that already have an active rule for the chosen period.
    If only_if_empty and any active rules exist for the profile, returns without creating.
    """
    as_of = as_of or date.today()
    pid = _resolve_profile_id(session, profile_id)
    if pid is None:
        return {"created": [], "count": 0, "skipped": 0, "message": "No profile"}

    existing = list_rules(session, pid)
    prior = (
        session.query(BudgetRule)
        .filter(BudgetRule.profile_id == pid)
        .first()
    )
    if only_if_empty and (existing or prior):
        return {
            "created": [],
            "count": 0,
            "skipped": len(existing) or 1,
            "message": "Budgets already exist — seed skipped.",
        }

    settings = _settings(session)
    mask = int(getattr(settings, "budget_workdays", DEFAULT_WORKDAYS) or DEFAULT_WORKDAYS)
    week_start = int(getattr(settings, "budget_week_starts_on", DEFAULT_WEEK_START) or 0)
    ruled = {(cid, r.period) for r in existing for cid in rule_member_ids(r)}
    owned = {cid for r in existing for cid in rule_member_ids(r)}

    span = categorized_spend_span(session, profile_id=pid, as_of=as_of)
    if span.get("analyzed_from"):
        lookback_start = date.fromisoformat(str(span["analyzed_from"]))
    else:
        lookback_start = as_of - timedelta(days=HISTORY_LOOKBACK_DAYS)
    rows = (
        session.query(
            Transaction.category_id,
            func.coalesce(func.sum(Transaction.amount), 0),
        )
        .filter(
            Transaction.profile_id == pid,
            Transaction.txn_date >= lookback_start,
            Transaction.txn_date <= as_of,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
            Transaction.category_id.isnot(None),
        )
        .group_by(Transaction.category_id)
        .order_by(func.sum(Transaction.amount).asc())
        .limit(20)
        .all()
    )

    created: list[dict[str, Any]] = []
    skip_groups = frozenset({"transfer", "income", "review"})
    for cat_id, _raw in rows:
        if cat_id is None or len(created) >= max_rules:
            break
        cid = int(cat_id)
        cat = session.get(Category, cid)
        bg = (cat.budget_group or "").lower() if cat else ""
        code = (cat.code or "") if cat else ""
        if bg in skip_groups or code.startswith("SYS_"):
            continue
        dates = expense_dates(
            session,
            profile_id=pid,
            category_id=cid,
            start=lookback_start,
            end=as_of,
        )
        cadence = infer_budget_cadence(dates)
        if len(dates) < 4:
            period = _preferred_period_for_category(cat)
            weekdays = mask
            cadence_reason = cadence.get("reason") or "category habit"
        else:
            period = cadence["period"]
            weekdays = int(cadence.get("active_weekdays") or mask)
            cadence_reason = cadence.get("reason") or ""
        if cid in owned or (cid, period) in ruled:
            continue
        first_seen = min(dates) if dates else lookback_start
        sug = suggest_for_category(
            session,
            profile_id=pid,
            category_id=cid,
            period=period,
            as_of=as_of,
            active_weekdays=weekdays,
            week_starts_on=week_start,
            history_start=first_seen,
        )
        raw_amt = _d(sug.get("suggested_amount"))
        amt = ceil_budget_amount(raw_amt, period)
        vendors = classify_vendors_for_categories(
            session, profile_id=pid, category_ids=[cid], as_of=as_of
        )
        has_bill = any(v.get("kind") in ("stable_bill", "variable_bill") for v in vendors)
        if amt < min_suggested and not has_bill:
            continue
        label = period_label(period, active_weekdays=weekdays)
        why = build_habit_why(
            plan_amount=amt,
            period_label_text=label,
            cadence_reason=cadence_reason,
            suggestion=sug,
            charge_count=len(dates),
            history_start=first_seen,
            as_of=as_of,
            raw_average=_d(sug.get("suggested_amount")),
        )
        rule = create_rule(
            session,
            profile_id=pid,
            category_id=cid,
            period=period,
            amount=amt,
            name=cat.display_name if cat else None,
            active_weekdays=weekdays,
            week_starts_on=week_start,
            source="accepted_suggestion",
            notes=json.dumps(why, ensure_ascii=False),
        )
        policy = apply_vendor_budget_policy(session, rule, as_of=as_of)
        ruled.add((cid, period))
        owned.add(cid)
        created.append(
            {
                "id": rule.id,
                "category_id": cid,
                "name": rule.name,
                "period": period,
                "period_label": label,
                "amount": str(rule.amount),
                "dropped": bool(policy.get("dropped")),
                "bills": policy.get("bills") or [],
            }
        )

    session.flush()
    return {
        "created": created,
        "count": len(created),
        "skipped": 0,
        "message": (
            f"Created {len(created)} budget(s) from spend history."
            if created
            else "No categories with enough spend history to seed."
        ),
        "status": budgets_status(session, profile_id=pid, as_of=as_of),
    }


def create_rule(
    session: Session,
    *,
    profile_id: int,
    category_id: int,
    period: str,
    amount: Decimal,
    name: str | None = None,
    active_weekdays: int | None = None,
    week_starts_on: int | None = None,
    source: str = "manual",
    notes: str | None = None,
) -> BudgetRule:
    settings = _settings(session)
    p = normalize_period(period)
    if p not in PERIODS:
        raise ValueError("period must be daily, weekly, biweekly, monthly, or yearly")
    # one active rule per profile+category+period
    existing = (
        session.query(BudgetRule)
        .filter(
            BudgetRule.profile_id == profile_id,
            BudgetRule.category_id == category_id,
            BudgetRule.period == p,
            BudgetRule.active.is_(True),
        )
        .first()
    )
    if existing:
        existing.amount = _d(amount)
        existing.name = name or existing.name
        existing.source = source
        existing.updated_at = datetime.now()
        if active_weekdays is not None:
            existing.active_weekdays = active_weekdays
        if week_starts_on is not None:
            existing.week_starts_on = week_starts_on
        session.flush()
        return existing
    rule = BudgetRule(
        profile_id=profile_id,
        category_id=category_id,
        period=p,
        amount=_d(amount),
        name=name,
        active_weekdays=active_weekdays
        if active_weekdays is not None
        else int(getattr(settings, "budget_workdays", DEFAULT_WORKDAYS) or DEFAULT_WORKDAYS),
        week_starts_on=week_starts_on
        if week_starts_on is not None
        else int(getattr(settings, "budget_week_starts_on", DEFAULT_WEEK_START) or 0),
        source=source,
        notes=notes,
        active=True,
    )
    session.add(rule)
    session.flush()
    return rule


def preview_cuts(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Chips: skip N lunch days, scale weekly remaining, release monthly discretionary."""
    as_of = as_of or date.today()
    st = budgets_status(session, profile_id=profile_id, as_of=as_of)
    offers: list[dict[str, Any]] = []
    for item in st.get("items") or []:
        if not item.get("active_today"):
            continue
        rem = _d(item.get("remaining"))
        plan = _d(item.get("plan"))
        if rem <= 0 and plan <= 0:
            continue
        period = item["period"]
        rid = item["id"]
        name = item["name"]
        if period == "daily" and (rem > 0 or plan > 0):
            unit = rem if rem > 0 else plan
            for n in (1, 2, 3, 5):
                free = (unit * n).quantize(Decimal("0.01"))
                if free <= 0:
                    continue
                offers.append(
                    {
                        "budget_rule_id": rid,
                        "kind": "skip_workdays",
                        "label": f"Skip {name} {n} workday(s)",
                        "detail": f"Free ${free} by not using this daily budget for {n} workday(s).",
                        "free_amount": str(free),
                        "params": {"workdays": n},
                    }
                )
        elif period == "weekly" and rem > 0:
            half = (rem * Decimal("0.5")).quantize(Decimal("0.01"))
            offers.append(
                {
                    "budget_rule_id": rid,
                    "kind": "scale_remaining",
                    "label": f"Cut {name} remaining 50% this week",
                    "detail": f"Free ${half} from this week's {name} budget.",
                    "free_amount": str(half),
                    "params": {"factor": "0.5"},
                }
            )
        elif period == "monthly" and rem > 0:
            offers.append(
                {
                    "budget_rule_id": rid,
                    "kind": "release_remaining",
                    "label": f"Release rest of {name} this month",
                    "detail": f"Free ${rem.quantize(Decimal('0.01'))} (pause remaining monthly budget).",
                    "free_amount": str(rem.quantize(Decimal("0.01"))),
                    "params": {},
                }
            )
    # best free first
    offers.sort(key=lambda o: _d(o["free_amount"]), reverse=True)
    return offers[:12]


def apply_cut(
    session: Session,
    *,
    budget_rule_id: int,
    kind: str,
    params: dict[str, Any] | None = None,
    as_of: date | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    rule = session.get(BudgetRule, budget_rule_id)
    if not rule or not rule.active:
        raise ValueError("Budget rule not found")
    params = params or {}
    kind = (kind or "").lower()
    if kind == "skip_workdays":
        n = int(params.get("workdays") or 1)
        days = next_workdays(as_of, n, int(rule.active_weekdays or DEFAULT_WORKDAYS))
        applies_to = days[-1] if days else as_of
    else:
        win = period_window(
            rule.period,
            as_of,
            week_starts_on=int(rule.week_starts_on or 0),
            active_weekdays=int(rule.active_weekdays or DEFAULT_WORKDAYS),
        )
        applies_to = win.end if win else as_of
    adj = BudgetAdjustment(
        budget_rule_id=rule.id,
        profile_id=rule.profile_id,
        kind=kind,
        params_json=json.dumps(params),
        applies_from=as_of,
        applies_to=applies_to,
        note=note,
    )
    session.add(adj)
    session.flush()
    return {
        "ok": True,
        "adjustment_id": adj.id,
        "status": budgets_status(session, profile_id=rule.profile_id, as_of=as_of),
    }


def home_budget_payload(
    session: Session,
    *,
    profile_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    st = budgets_status(session, profile_id=profile_id, as_of=as_of)
    cuts = preview_cuts(session, profile_id=profile_id, as_of=as_of)
    # compact summary for Home cards
    summary = []
    for period in ("daily", "weekly", "monthly"):
        for item in st.get("by_period", {}).get(period) or []:
            if item.get("active_today") or period != "daily":
                if period == "daily" and not item.get("active_today"):
                    continue
                summary.append(
                    {
                        "name": item["name"],
                        "period": period,
                        "remaining": item["remaining"],
                        "plan": item["plan"],
                        "actual": item["actual"],
                        "status": item["status"],
                    }
                )
    return {
        "reserve_enabled": st.get("reserve_enabled"),
        "reserve_total": st.get("reserve_total"),
        "summary": summary[:8],
        "cut_offers": cuts[:6],
        "over_count": sum(1 for i in st.get("items") or [] if i.get("status") == "over"),
    }
