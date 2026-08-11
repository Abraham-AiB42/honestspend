"""Period budgets: status, historical suggestions, reserve, cut offers."""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from financial_os.db import (
    AppSettings,
    BudgetAdjustment,
    BudgetRule,
    Category,
    Profile,
    Transaction,
)
from financial_os.services.budget_periods import (
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
    category_id: int,
    start: date,
    end: date,
) -> Decimal:
    """Sum abs(outflows) for category in [start, end] inclusive. Excludes transfers."""
    q = (
        session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.profile_id == profile_id,
            Transaction.category_id == category_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.is_transfer.is_(False),
            Transaction.amount < 0,
        )
    )
    raw = q.scalar()
    # amount is negative for outflows → abs
    return abs(_d(raw))


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
    if win is None:
        # non-workday for daily rule
        cat = session.get(Category, rule.category_id)
        return {
            "id": rule.id,
            "profile_id": rule.profile_id,
            "category_id": rule.category_id,
            "category_name": cat.display_name if cat else str(rule.category_id),
            "name": rule.name or (cat.display_name if cat else "Budget"),
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
        category_id=rule.category_id,
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
    cat = session.get(Category, rule.category_id)
    st = "ok"
    if over > 0:
        st = "over"
    elif plan > 0 and remaining <= plan * Decimal("0.15"):
        st = "tight"
    return {
        "id": rule.id,
        "profile_id": rule.profile_id,
        "category_id": rule.category_id,
        "category_name": cat.display_name if cat else str(rule.category_id),
        "name": rule.name or (cat.display_name if cat else "Budget"),
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
    # Honesty: do not stack daily+weekly+monthly for the same category.
    # Reserve = sum over categories of max(remaining) across periods.
    best_by_cat: dict[int, Decimal] = {}
    for rule in list_rules(session, pid):
        st = rule_status(session, rule, as_of=as_of)
        if st:
            items.append(st)
            if st.get("active_today"):
                cid = int(st.get("category_id") or 0)
                rem = _d(st.get("remaining"))
                if rem < ZERO:
                    rem = ZERO
                if cid > 0:
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
        "reserve_mode": "max_remaining_per_category",
        "items": items,
        "by_period": {
            "daily": [i for i in items if i["period"] == "daily"],
            "weekly": [i for i in items if i["period"] == "weekly"],
            "monthly": [i for i in items if i["period"] == "monthly"],
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
        from financial_os.db import Profile

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
) -> dict[str, Any]:
    as_of = as_of or date.today()
    p = (period or "monthly").lower()
    cat = session.get(Category, category_id)
    name = cat.display_name if cat else str(category_id)

    samples: list[Decimal] = []
    if p == "daily":
        start = as_of - timedelta(days=DAILY_LOOKBACK_DAYS)
        samples = _daily_series(
            session, profile_id, category_id, start, as_of - timedelta(days=1), active_weekdays
        )
        window_desc = f"{DAILY_LOOKBACK_DAYS}d workdays"
    elif p == "weekly":
        samples = []
        cur_end = as_of - timedelta(days=1)
        for _ in range(WEEKLY_LOOKBACK_WEEKS):
            ws, we = week_bounds(cur_end, week_starts_on)
            samples.append(
                expense_actual(
                    session,
                    profile_id=profile_id,
                    category_id=category_id,
                    start=ws,
                    end=we,
                )
            )
            cur_end = ws - timedelta(days=1)
        samples.reverse()
        window_desc = f"{WEEKLY_LOOKBACK_WEEKS} weeks"
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
            samples.append(
                expense_actual(
                    session,
                    profile_id=profile_id,
                    category_id=category_id,
                    start=ms,
                    end=me,
                )
            )
            if m == 1:
                y, m = y - 1, 12
            else:
                m -= 1
        samples.reverse()
        window_desc = f"{MONTHLY_LOOKBACK_MONTHS} months"

    nonzero = [s for s in samples if s > 0]
    use = samples  # include zeros for daily (days with no spend)
    if p != "daily":
        use = samples

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
    if only_if_empty and existing:
        return {
            "created": [],
            "count": 0,
            "skipped": len(existing),
            "message": "Budgets already exist — seed skipped.",
        }

    settings = _settings(session)
    mask = int(getattr(settings, "budget_workdays", DEFAULT_WORKDAYS) or DEFAULT_WORKDAYS)
    week_start = int(getattr(settings, "budget_week_starts_on", DEFAULT_WEEK_START) or 0)
    ruled = {(r.category_id, r.period) for r in existing}

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
        .order_by(func.sum(Transaction.amount).asc())
        .limit(20)
        .all()
    )

    created: list[dict[str, Any]] = []
    for cat_id, _raw in rows:
        if cat_id is None or len(created) >= max_rules:
            break
        cid = int(cat_id)
        cat = session.get(Category, cid)
        if cat and (cat.budget_group or "").lower() in ("transfer", "income", "review"):
            continue
        period = _preferred_period_for_category(cat)
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
        amt = _d(sug.get("suggested_amount"))
        if amt < min_suggested:
            continue
        # Round friendly: daily to nearest dollar, weekly/monthly to nearest 5
        if period == "daily":
            amt = max(Decimal("1"), amt.quantize(Decimal("1")))
        else:
            amt = max(Decimal("5"), (amt / 5).quantize(Decimal("1")) * 5)
        rule = create_rule(
            session,
            profile_id=pid,
            category_id=cid,
            period=period,
            amount=amt,
            name=cat.display_name if cat else None,
            active_weekdays=mask,
            week_starts_on=week_start,
            source="accepted_suggestion",
            notes=f"Seeded from history ({sug.get('window')})",
        )
        ruled.add((cid, period))
        created.append(
            {
                "id": rule.id,
                "category_id": cid,
                "name": rule.name,
                "period": period,
                "amount": str(rule.amount),
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
    p = (period or "monthly").lower()
    if p not in ("daily", "weekly", "monthly"):
        raise ValueError("period must be daily, weekly, or monthly")
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
