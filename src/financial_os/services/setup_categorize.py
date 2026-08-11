"""Setup wizard: auto-categorize high-confidence + confirm top ambiguous payees."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from financial_os.db import Category, Profile, Transaction
from financial_os.services.categorizer import (
    categorize_uncategorized,
    learn_rule_from_correction,
)
from financial_os.services.recurring_detect import normalize_payee

ZERO = Decimal("0")
DEFAULT_CONFIRM_CAP = 20


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _profile_id(session: Session, profile_id: int | None) -> int | None:
    if profile_id is not None:
        return int(profile_id)
    d = session.query(Profile).filter(Profile.is_default.is_(True)).first()
    if d:
        return int(d.id)
    p = session.query(Profile).filter(Profile.slug == "personal").first()
    return int(p.id) if p else None


def auto_apply_high_confidence(
    session: Session,
    *,
    profile_id: int | None = None,
    limit: int = 300,
    min_confidence: float = 0.85,
    use_grok: bool = False,
) -> dict[str, Any]:
    """Apply rule-based (and optional Grok) suggestions above confidence floor."""
    pid = _profile_id(session, profile_id)
    results = categorize_uncategorized(
        session,
        profile_id=pid,
        limit=limit,
        apply=True,
        use_grok=use_grok,
        min_confidence=min_confidence,
    )
    applied = sum(1 for r in results if r.get("applied"))
    return {
        "scanned": len(results),
        "applied": applied,
        "min_confidence": min_confidence,
        "use_grok": use_grok,
        "message": f"Auto-categorized {applied} of {len(results)} uncategorized transaction(s).",
    }


def categorize_status(
    session: Session,
    *,
    profile_id: int | None = None,
    confirm_cap: int = DEFAULT_CONFIRM_CAP,
) -> dict[str, Any]:
    """Uncategorized counts + top payee groups for user confirm."""
    pid = _profile_id(session, profile_id)
    q = session.query(Transaction).filter(
        Transaction.category_id.is_(None),
        Transaction.is_transfer.is_(False),
        Transaction.status != "void",
        Transaction.amount < 0,
    )
    if pid is not None:
        q = q.filter(Transaction.profile_id == pid)

    total_uncat = q.count()
    total_exp = (
        session.query(func.count(Transaction.id))
        .filter(
            Transaction.is_transfer.is_(False),
            Transaction.status != "void",
            Transaction.amount < 0,
            *([Transaction.profile_id == pid] if pid is not None else []),
        )
        .scalar()
        or 0
    )
    categorized = max(0, int(total_exp) - int(total_uncat))

    by_payee: dict[str, list[Transaction]] = defaultdict(list)
    for t in q.order_by(Transaction.txn_date.desc()).limit(800).all():
        key = normalize_payee(t.payee) or (t.payee or "").strip().lower()[:40]
        if len(key) < 2:
            key = f"txn-{t.id}"
        by_payee[key].append(t)

    groups: list[dict[str, Any]] = []
    for key, txns in by_payee.items():
        amounts = [abs(_d(t.amount)) for t in txns]
        total = sum(amounts)
        display = (txns[0].payee or key)[:80]
        groups.append(
            {
                "payee_key": key,
                "payee": display,
                "count": len(txns),
                "total_abs": str(total.quantize(Decimal("0.01"))),
                "sample_transaction_id": txns[0].id,
                "transaction_ids": [t.id for t in txns[:50]],
            }
        )
    # Prioritize by $ impact then frequency
    groups.sort(key=lambda g: (-float(g["total_abs"]), -int(g["count"])))
    queue = groups[: max(1, min(confirm_cap, 25))]

    # Category chips: personal budget groups + common names
    cq = session.query(Category)
    if pid is not None:
        cq = cq.filter((Category.profile_id == pid) | (Category.profile_id.is_(None)))
    cats = cq.order_by(Category.display_name).limit(80).all()
    chips = []
    for c in cats:
        bg = (c.budget_group or "").lower()
        if bg in ("transfer", "income", "review"):
            continue
        chips.append(
            {
                "id": c.id,
                "name": c.display_name,
                "budget_group": c.budget_group,
                "code": c.code,
            }
        )
    # Prefer food, transport, utilities first in UI order
    priority = {"food": 0, "transport": 1, "housing": 2, "utilities": 3, "shopping": 4}
    chips.sort(key=lambda x: (priority.get((x.get("budget_group") or "").lower(), 50), x["name"]))

    pct = 100.0 if total_exp == 0 else round(100.0 * categorized / total_exp, 1)
    return {
        "profile_id": pid,
        "expense_count": int(total_exp),
        "uncategorized_count": int(total_uncat),
        "categorized_count": categorized,
        "categorized_pct": pct,
        "confirm_queue": queue,
        "confirm_cap": confirm_cap,
        "category_chips": chips[:40],
        "done_enough": int(total_uncat) == 0 or len(queue) == 0 or pct >= 70,
        "message": (
            f"{categorized} categorized · {total_uncat} left. "
            f"Confirm up to {len(queue)} top payees (or skip)."
            if total_uncat
            else "All expenses categorized — nice."
        ),
    }


def confirm_payee_category(
    session: Session,
    *,
    payee_key: str | None = None,
    transaction_ids: list[int] | None = None,
    category_id: int,
    create_rule: bool = True,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Assign category to all matching uncategorized txns; optional rule."""
    cat = session.get(Category, category_id)
    if not cat:
        raise ValueError("Category not found")

    pid = _profile_id(session, profile_id)
    updated = 0
    sample: Transaction | None = None

    if transaction_ids:
        for tid in transaction_ids:
            t = session.get(Transaction, tid)
            if not t or t.category_id is not None:
                continue
            if pid is not None and t.profile_id != pid:
                continue
            t.category_id = category_id
            t.confidence = Decimal("1")
            updated += 1
            sample = sample or t
    elif payee_key:
        key = normalize_payee(payee_key) or payee_key.strip().lower()
        q = session.query(Transaction).filter(
            Transaction.category_id.is_(None),
            Transaction.status != "void",
        )
        if pid is not None:
            q = q.filter(Transaction.profile_id == pid)
        for t in q.limit(500).all():
            nk = normalize_payee(t.payee) or (t.payee or "").strip().lower()
            if nk == key or (key and key in nk):
                t.category_id = category_id
                t.confidence = Decimal("1")
                updated += 1
                sample = sample or t
    else:
        raise ValueError("payee_key or transaction_ids required")

    rule_id = None
    if create_rule and sample is not None:
        rule = learn_rule_from_correction(session, sample, category_id)
        if rule:
            rule_id = rule.id

    session.flush()
    return {
        "ok": True,
        "updated": updated,
        "category_id": category_id,
        "category_name": cat.display_name,
        "rule_id": rule_id,
        "status": categorize_status(session, profile_id=pid),
    }
