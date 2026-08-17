"""Setup wizard budgets seed + buffer configuration."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, AppSettings, Category, Profile
from honestspend.services.budget_service import (
    REVIEW_AMOUNT_FLOOR,
    budget_line_activity,
    categorized_spend_span,
    category_payload,
    create_rule,
    find_rule_for_category,
    line_name_for_categories,
    list_rules,
    move_budget_category,
    period_label,
    recalculate_rule_from_history,
    rule_member_ids,
    seed_from_history,
    set_rule_members,
    why_for_rule,
)
from honestspend.services.setup_categorize import list_categorize_books

ZERO = Decimal("0")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v).replace("$", "").replace(",", "").replace("(", "-").replace(")", "").strip() or "0")


def _money(v: Any) -> str:
    amt = _d(v).quantize(Decimal("0.01"))
    sign = "-" if amt < 0 else ""
    return f"{sign}${abs(amt):,.2f}"


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
    books = list_categorize_books(session)
    pid = _pid(session, profile_id)
    if profile_id is None and books:
        leftover = next((b for b in books if int(b.get("id") or 0) == int(pid or 0)), books[0])
        pid = int(leftover["id"])
    seed_result = None
    if seed_if_empty:
        targets = [int(b["id"]) for b in books] if books else ([int(pid)] if pid else [])
        seeded = []
        for tid in targets:
            seeded.append(seed_from_history(session, profile_id=tid, only_if_empty=True, max_rules=12))
        seed_result = seeded[0] if len(seeded) == 1 else {"books": seeded, "count": sum(s.get("count") or 0 for s in seeded)}
    rules = list_rules(session, pid)
    from datetime import date as date_cls

    span = (
        categorized_spend_span(session, profile_id=int(pid), as_of=date_cls.today())
        if pid is not None
        else {
            "analyzed_from": None,
            "analyzed_to": None,
            "lookback_days": None,
            "analyzed_from_label": None,
            "analyzed_to_label": None,
        }
    )
    items = []
    for r in rules:
        mask = int(r.active_weekdays) if r.active_weekdays is not None else None
        label = period_label(r.period, active_weekdays=mask)
        why = why_for_rule(session, r)
        members = rule_member_ids(r)
        member_cats = category_payload(session, members)
        display = line_name_for_categories(session, members) if members else (r.name or "Budget")
        items.append(
            {
                "id": r.id,
                "name": display,
                "category_id": r.category_id,
                "period": r.period,
                "period_label": label,
                "amount": str(r.amount),
                "amount_display": _money(r.amount),
                "source": r.source,
                "recommended": bool(why),
                "why": why,
                "active_weekdays": r.active_weekdays,
                "member_categories": member_cats,
                "over_review_floor": _d(r.amount) >= REVIEW_AMOUNT_FLOOR,
                "activity": budget_line_activity(session, r),
            }
        )
    for it in items:
        rid = int(it["id"])
        it["move_targets"] = [
            {
                "id": o["id"],
                "name": o["name"],
                "period_label": o["period_label"],
            }
            for o in items
            if int(o["id"]) != rid
        ]
    cats = []
    reassign_cats = []
    if pid is not None:
        hide = {"transfer", "income", "review"}
        for c in (
            session.query(Category)
            .filter((Category.profile_id == int(pid)) | (Category.profile_id.is_(None)))
            .order_by(Category.display_name)
            .all()
        ):
            row = {"id": int(c.id), "name": c.display_name or c.code or f"Category {c.id}"}
            if (c.code or "").startswith("SYS_") and (c.budget_group or "").lower() == "review":
                continue
            reassign_cats.append(row)
            if (c.budget_group or "").lower() in hide:
                continue
            if (c.code or "").startswith("SYS_"):
                continue
            cats.append(row)
    from_s = span.get("analyzed_from")
    to_s = span.get("analyzed_to")
    days = span.get("lookback_days")
    from_l = span.get("analyzed_from_label") or from_s
    to_l = span.get("analyzed_to_label") or to_s
    if from_s and to_s:
        span_bit = f" ({days} days of books)" if days else ""
        lookback_message = (
            f"We analyzed spending from {from_l} to {to_l}{span_bit}. "
            "Each line’s period (per day, per workday, weekly, bi-weekly, monthly, or annual) "
            "comes from how often that category was charged."
        )
    else:
        lookback_message = "No categorized spend in the books yet — add lines by hand or categorize more."
    book = next((b for b in books if int(b.get("id") or 0) == int(pid or 0)), None)
    book_name = (book or {}).get("display_name") or "This book"
    return {
        "profile_id": pid,
        "book_name": book_name,
        "book_entity_type": (book or {}).get("entity_type") or "personal",
        "books": books,
        "rules": items,
        "count": len(items),
        "seed": seed_result,
        "categories": cats,
        "reassign_categories": reassign_cats,
        "analyzed_from": from_s,
        "analyzed_to": to_s,
        "analyzed_from_label": from_l,
        "analyzed_to_label": to_l,
        "lookback_days": days,
        "lookback_message": lookback_message,
        "message": (
            f"{len(items)} budget plan(s) for {book_name} from prior spending. Adjust, add, or delete."
            if items
            else f"No budgets yet for {book_name} — add a line, or categorize more spend history."
        ),
    }


def reassign_budget_transaction(
    session: Session,
    *,
    transaction_id: int,
    category_id: int | None = None,
    profile_id: int | None = None,
    all_same_payee: bool = False,
) -> dict[str, Any]:
    """Recategorize a charge or move it onto another book."""
    from honestspend.db import Transaction
    from honestspend.services.recurring_detect import categorize_payee_key
    from honestspend.services.setup_categorize import _bind_category_to_txn, _sync_transfer_flag

    t = session.get(Transaction, int(transaction_id))
    if not t:
        return {"ok": False, "error": "Charge not found"}
    targets = [t]
    if all_same_payee and category_id:
        key = categorize_payee_key(t.payee)
        book_id = int(t.profile_id)
        extras: list[Any] = []
        for other in (
            session.query(Transaction)
            .filter(
                Transaction.profile_id == book_id,
                Transaction.amount < 0,
                Transaction.status != "void",
                Transaction.id != t.id,
            )
            .all()
        ):
            if categorize_payee_key(other.payee) == key:
                extras.append(other)
        targets.extend(extras)
    updated_ids: list[int] = []
    for row in targets:
        if profile_id and not all_same_payee:
            dest = session.get(Profile, int(profile_id))
            if dest and int(row.profile_id) != int(dest.id):
                old_cat = session.get(Category, row.category_id) if row.category_id else None
                memo = row.memo or ""
                if "[orig-profile:" not in memo:
                    row.memo = f"{memo} [orig-profile:{row.profile_id}]".strip()
                row.profile_id = int(dest.id)
                if old_cat:
                    alt = (
                        session.query(Category)
                        .filter(
                            Category.profile_id == dest.id,
                            Category.display_name == old_cat.display_name,
                        )
                        .first()
                    )
                    if alt:
                        row.category_id = alt.id
        if category_id:
            cat = session.get(Category, int(category_id))
            if not cat:
                return {"ok": False, "error": "Category not found", "transaction_id": t.id}
            bound = _bind_category_to_txn(session, cat, row)
            row.category_id = bound.id
            _sync_transfer_flag(row, bound, session)
        updated_ids.append(int(row.id))
    session.flush()
    return {
        "ok": True,
        "reassigned": True,
        "transaction_id": int(t.id),
        "transaction_ids": updated_ids,
        "updated": len(updated_ids),
        "category_id": int(t.category_id) if t.category_id else None,
        "profile_id": int(t.profile_id) if t.profile_id else None,
    }


def apply_budget_edits(
    session: Session,
    *,
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """updates: [{id, amount}] or [{id, drop: true}] or [{create: true, category_id, period, amount}]"""
    from honestspend.db import BudgetRule

    changed = []
    for u in updates:
        if u.get("create"):
            try:
                pid = int(u.get("profile_id") or 0)
                cid = int(u.get("category_id") or 0)
                amt = _d(u.get("amount"))
            except Exception:
                continue
            if pid <= 0 or cid <= 0:
                continue
            raw_period = str(u.get("period") or "monthly")
            weekdays = u.get("active_weekdays")
            if raw_period in ("daily_workday", "workday", "per_workday"):
                period = "daily"
                if weekdays is None:
                    weekdays = 31
            elif raw_period in ("daily", "per_day", "day", "daily_all"):
                period = "daily"
                if weekdays is None:
                    weekdays = 127
            else:
                period = raw_period
            try:
                weekdays_i = int(weekdays) if weekdays is not None else None
            except (TypeError, ValueError):
                weekdays_i = None
            cat = session.get(Category, cid)
            rule = create_rule(
                session,
                profile_id=pid,
                category_id=cid,
                period=period,
                amount=amt,
                name=(u.get("name") or (cat.display_name if cat else None)),
                active_weekdays=weekdays_i,
                source="manual",
            )
            owner = find_rule_for_category(
                session, profile_id=pid, category_id=cid, exclude_rule_id=int(rule.id)
            )
            if owner:
                leftover = [m for m in rule_member_ids(owner) if m != cid]
                if leftover:
                    set_rule_members(session, owner, leftover)
                else:
                    owner.active = False
            changed.append({"id": rule.id, "created": True, "amount": str(rule.amount)})
            continue
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
        if u.get("recalculate"):
            changed.append(recalculate_rule_from_history(session, rule))
            continue
        if u.get("transaction_id"):
            try:
                tid = int(u.get("transaction_id") or 0)
            except (TypeError, ValueError):
                continue
            if tid <= 0:
                continue
            cat_raw = u.get("category_id")
            book_raw = u.get("profile_id")
            try:
                new_cat = int(cat_raw) if cat_raw not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                new_cat = None
            try:
                new_book = int(book_raw) if book_raw not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                new_book = None
            changed.append(
                reassign_budget_transaction(
                    session,
                    transaction_id=tid,
                    category_id=new_cat,
                    profile_id=new_book,
                    all_same_payee=bool(u.get("all_same_payee")),
                )
            )
            continue
        if u.get("move_category_id"):
            try:
                cid = int(u.get("move_category_id") or 0)
            except (TypeError, ValueError):
                continue
            if cid <= 0:
                continue
            dest_raw = u.get("to_rule_id")
            try:
                dest_id = int(dest_raw) if dest_raw not in (None, "", 0, "0") else None
            except (TypeError, ValueError):
                dest_id = None
            moved = move_budget_category(
                session,
                from_rule_id=rid,
                category_id=cid,
                to_rule_id=dest_id,
                to_new=bool(u.get("to_new")),
            )
            changed.append(moved)
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
    from honestspend.engine.ifpp import CashAccountView, effective_safety_buffer

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
