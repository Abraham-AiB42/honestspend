"""Setup wizard budgets seed + buffer configuration."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import Account, AppSettings, Profile
from financial_os.services.budget_service import list_rules, seed_from_history

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _pid(session: Session, profile_id: int | None) -> int | None:
    if profile_id is not None:
        return int(profile_id)
    d = session.query(Profile).filter(Profile.is_default.is_(True)).first()
    if d:
        return int(d.id)
    p = session.query(Profile).filter(Profile.slug == "personal").first()
    return int(p.id) if p else None


def budgets_review(
    session: Session,
    *,
    profile_id: int | None = None,
    seed_if_empty: bool = True,
) -> dict[str, Any]:
    """Seed from history if empty, return rules for review."""
    pid = _pid(session, profile_id)
    seed_result = None
    if seed_if_empty:
        seed_result = seed_from_history(session, profile_id=pid, only_if_empty=True, max_rules=12)
    rules = list_rules(session, pid)
    items = [
        {
            "id": r.id,
            "name": r.name,
            "category_id": r.category_id,
            "period": r.period,
            "amount": str(r.amount),
            "source": r.source,
        }
        for r in rules
    ]
    return {
        "profile_id": pid,
        "rules": items,
        "count": len(items),
        "seed": seed_result,
        "message": (
            f"{len(items)} budget plan(s). Adjust amounts or continue."
            if items
            else "No budgets yet — categorize more spend history, or skip."
        ),
    }


def apply_budget_edits(
    session: Session,
    *,
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """updates: [{id, amount}] or [{id, drop: true}]"""
    from financial_os.db import BudgetRule

    changed = []
    for u in updates:
        rid = int(u.get("id") or 0)
        if rid <= 0:
            continue
        rule = session.get(BudgetRule, rid)
        if not rule:
            continue
        if u.get("drop") or u.get("delete"):
            rule.active = False
            changed.append({"id": rid, "dropped": True})
            continue
        if "amount" in u:
            try:
                amt = _d(u["amount"])
            except Exception:
                continue
            rule.amount = amt
            changed.append({"id": rid, "amount": str(rule.amount)})
    session.flush()
    return {"ok": True, "changed": changed, "count": len(changed)}


def buffers_status(session: Session, *, profile_id: int | None = None) -> dict[str, Any]:
    """Buffer status aligned with IFPP pool (is_cash_for_ifpp + never_negative_scope)."""
    from financial_os.engine.ifpp import CashAccountView, effective_safety_buffer

    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    pid = _pid(session, profile_id)
    scope = (getattr(settings, "never_negative_scope", None) or "checking").lower()
    q = session.query(Account).filter(
        Account.archived_at.is_(None),
        Account.kind.in_(("checking", "savings", "cash")),
    )
    if pid is not None:
        q = q.filter(Account.profile_id == pid)
    rows = q.order_by(Account.id).all()
    views = [
        CashAccountView(
            id=a.id,
            name=a.nickname,
            balance=_d(a.current_balance),
            is_cash_for_ifpp=bool(a.is_cash_for_ifpp),
            kind=a.kind or "checking",
            safety_buffer=_d(getattr(a, "safety_buffer", None) or 0),
        )
        for a in rows
    ]
    eff = effective_safety_buffer(
        views,
        total_buffer=_d(settings.safety_buffer),
        never_negative_scope=scope,
    )
    # Display all cash accounts; mark which are in IFPP pool
    if scope == "checking":
        pool_ids = {v.id for v in views if v.is_cash_for_ifpp and v.kind == "checking"}
        if not pool_ids:
            pool_ids = {v.id for v in views if v.is_cash_for_ifpp}
    else:
        pool_ids = {v.id for v in views if v.is_cash_for_ifpp}

    accounts = []
    for a in rows:
        buf = _d(getattr(a, "safety_buffer", None) or 0)
        bal = _d(a.current_balance)
        reserved = min(buf, max(ZERO, bal)) if buf > 0 else ZERO
        in_pool = a.id in pool_ids
        accounts.append(
            {
                "id": a.id,
                "nickname": a.nickname,
                "kind": a.kind,
                "balance": str(bal),
                "safety_buffer": str(buf) if getattr(a, "safety_buffer", None) is not None else None,
                "is_cash_for_ifpp": bool(a.is_cash_for_ifpp),
                "in_ifpp_pool": in_pool,
                "available_after_buffer": str(max(ZERO, bal - reserved)),
            }
        )
    total = eff["total_floor"]
    per_sum = eff["per_account_sum"]
    effective = eff["effective"]
    return {
        "total_buffer": str(total),
        "per_account_sum": str(per_sum),
        "effective_buffer": str(effective),
        "never_negative_scope": scope,
        "pool_account_ids": sorted(pool_ids),
        "accounts": accounts,
        "message": (
            f"Total floor ${total} · per-account in IFPP pool ${per_sum} · "
            f"IFPP uses max = ${effective}."
        ),
    }


def save_buffers(
    session: Session,
    *,
    total_buffer: Decimal | None = None,
    account_buffers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """account_buffers: [{id, safety_buffer}]"""
    settings = session.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        session.add(settings)
    if total_buffer is not None:
        settings.safety_buffer = max(ZERO, _d(total_buffer))
    for row in account_buffers or []:
        aid = int(row.get("id") or 0)
        if aid <= 0:
            continue
        acct = session.get(Account, aid)
        if not acct:
            continue
        if row.get("safety_buffer") is None and "safety_buffer" not in row:
            continue
        val = row.get("safety_buffer")
        if val is None or val == "":
            acct.safety_buffer = None
        else:
            acct.safety_buffer = max(ZERO, _d(val))
    session.flush()
    return buffers_status(session)
