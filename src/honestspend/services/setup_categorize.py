"""Setup wizard: auto-categorize high-confidence + confirm top ambiguous payees."""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from honestspend.db import Account, Category, Profile, Transaction
from honestspend.services.categorizer import (
    categorize_uncategorized,
    learn_rule_from_correction,
)
from honestspend.services.recurring_detect import (
    categorize_payee_key,
    is_check_payee,
    normalize_payee,
)

ZERO = Decimal("0")
DEFAULT_CONFIRM_CAP = 20


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _infer_counterparty(
    session: Session,
    payee: str,
    *,
    exclude_ids: set[int],
) -> str | None:
    """Best-effort 'to' account from payee text or books nicknames."""
    from honestspend.services.payment_match import infer_dest_account_name

    return infer_dest_account_name(
        session,
        payee,
        exclude_ids=exclude_ids,
        from_account_id=next(iter(exclude_ids), None) if exclude_ids else None,
    )


def _void_promo_disclosure_txns(session: Session, *, profile_id: int | None) -> int:
    """Drop imported promo APR / deferred-interest table rows from spend."""
    from honestspend.services.statement_pdf import is_promo_disclosure_line
    from honestspend.services.txn_void import void_transaction

    q = session.query(Transaction).filter(
        Transaction.status != "void",
        Transaction.category_id.is_(None),
    )
    if profile_id is not None:
        q = q.filter(Transaction.profile_id == profile_id)
    n = 0
    for t in q.order_by(Transaction.id.desc()).limit(1500).all():
        if not is_promo_disclosure_line(t.payee or "", payee=t.payee):
            continue
        # Only void disclosure fragments, never a normal merchant payee.
        pay = (t.payee or "").strip()
        if not is_promo_disclosure_line(pay, payee=pay):
            continue
        try:
            void_transaction(session, t.id, reason="promo APR line, not a charge")
            n += 1
        except Exception:
            continue
    return n


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
    prep = prepare_categorize(session, profile_id=pid)
    pay_linked = int(prep.get("payments_linked") or 0)
    results = categorize_uncategorized(
        session,
        profile_id=pid,
        limit=limit,
        apply=True,
        use_grok=use_grok,
        min_confidence=min_confidence,
    )
    applied = sum(1 for r in results if r.get("applied"))
    applied_ids = [int(r["transaction_id"]) for r in results if r.get("applied") and r.get("transaction_id")]
    pair_ids: list[int] = []
    for p in prep.get("pairs") or []:
        for k in ("out_txn_id", "in_txn_id"):
            if p.get(k):
                pair_ids.append(int(p[k]))
    return {
        "scanned": len(results),
        "applied": applied,
        "min_confidence": min_confidence,
        "use_grok": use_grok,
        "payments_linked": pay_linked,
        "message": (
            f"Auto-categorized {applied} of {len(results)} uncategorized transaction(s)"
            + (f" · matched {pay_linked} payment(s) between accounts" if pay_linked else "")
            + "."
        ),
        "status": categorize_status(
            session,
            profile_id=pid,
            recent_txn_ids=applied_ids + pair_ids,
            recent_pairs=list(prep.get("pairs") or []),
        ),
    }


def prepare_categorize(session: Session, *, profile_id: int | None = None) -> dict[str, Any]:
    """Write-side setup: COA backfill, seed rules, payment pairs, void promo rows."""
    pid = _profile_id(session, profile_id)
    pairs: list[dict[str, Any]] = []
    from honestspend.seed import ensure_coa_categories
    from honestspend.services.categorizer import ensure_seed_rules
    from honestspend.services.payment_match import auto_link_account_payments

    ensure_coa_categories(session)
    ensure_seed_rules(session)
    linked = auto_link_account_payments(session)
    pairs = list(linked.get("pairs") or [])
    _void_promo_disclosure_txns(session, profile_id=pid)
    session.flush()
    return {"payments_linked": int(linked.get("linked") or 0), "pairs": pairs}


def categorize_status(
    session: Session,
    *,
    profile_id: int | None = None,
    confirm_cap: int = DEFAULT_CONFIRM_CAP,
    recent_txn_ids: list[int] | None = None,
    recent_pairs: list[dict[str, Any]] | None = None,
    recent_rule_id: int | None = None,
) -> dict[str, Any]:
    """Uncategorized counts + top payee groups for user confirm. Read-only."""
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
        key = categorize_payee_key(t.payee)
        if len(key) < 2:
            key = f"txn-{t.id}"
        by_payee[key].append(t)

    groups: list[dict[str, Any]] = []
    for key, txns in by_payee.items():
        amounts = [abs(_d(t.amount)) for t in txns]
        total = sum(amounts)
        display = (txns[0].payee or key)[:80]
        pid_votes: dict[int | None, int] = defaultdict(int)
        from_names: list[str] = []
        seen_from: set[int] = set()
        for t in txns:
            pid_votes[t.profile_id] += 1
            if t.account_id and t.account_id not in seen_from:
                seen_from.add(t.account_id)
                acct = t.account or session.get(Account, t.account_id)
                if acct:
                    from_names.append(acct.nickname)
        group_pid = max(pid_votes, key=pid_votes.get) if pid_votes else pid
        to_name = _infer_counterparty(session, display, exclude_ids=seen_from)
        if not to_name:
            pair_id = txns[0].transfer_pair_id
            if pair_id:
                pair = session.get(Transaction, pair_id)
                if pair and pair.account_id not in seen_from:
                    pa = pair.account or session.get(Account, pair.account_id)
                    if pa:
                        to_name = pa.nickname
        groups.append(
            {
                "payee_key": key,
                "payee": display,
                "count": len(txns),
                "total_abs": str(total.quantize(Decimal("0.01"))),
                "sample_transaction_id": txns[0].id,
                "transaction_ids": [t.id for t in txns[:50]],
                "profile_id": group_pid,
                "from_accounts": from_names,
                "to_account": to_name,
            }
        )
    # Prioritize by $ impact then frequency
    groups.sort(key=lambda g: (-float(g["total_abs"]), -int(g["count"])))
    queue = groups[: max(1, min(confirm_cap, 25))]

    chip_pid = queue[0].get("profile_id") if queue else pid
    biz_ids = [
        int(p.id)
        for p in session.query(Profile).all()
        if (p.entity_type or "").lower() in ("business", "s_corp", "llc")
        or (p.tax_form_primary or "").upper() in ("1120S", "1065", "SCHC", "1120")
    ]
    cq = session.query(Category)
    allowed = set(biz_ids)
    if chip_pid is not None:
        allowed.add(int(chip_pid))
    if allowed:
        cq = cq.filter((Category.profile_id.in_(allowed)) | (Category.profile_id.is_(None)))
    cats = cq.order_by(Category.display_name).all()
    hide_groups = {
        "income",
        "review",
        "tax_special",
        "tax_itemized",
        "tax_adjustment",
        "tax_payment",
        "assets",
        "cogs",
    }
    hide_name = {
        "depletion",
        "energy efficient commercial buildings",
        "bad debts",
        "startup costs",
        "returns and allowances",
        "section 179 (shareholder)",
        "uncategorized",
    }
    chips = []
    more = []
    seen_chip: set[str] = set()
    seen_more: set[str] = set()
    prefer_pid = chip_pid
    ordered = sorted(
        cats,
        key=lambda c: (
            0 if prefer_pid is not None and c.profile_id == prefer_pid else 1,
            (c.display_name or "").casefold(),
        ),
    )
    for c in ordered:
        bg = (c.budget_group or "").lower()
        name = (c.display_name or "").strip()
        item = {
            "id": c.id,
            "name": name,
            "budget_group": c.budget_group,
            "code": c.code,
        }
        if name.lower() in hide_name:
            continue
        key = name.casefold()
        to_more = bg in hide_groups or (c.scope or "") == "business"
        if to_more:
            if key in seen_more:
                continue
            seen_more.add(key)
            more.append(item)
            continue
        if key in seen_chip:
            continue
        seen_chip.add(key)
        chips.append(item)
    chips.sort(key=lambda x: (x.get("name") or "").casefold())
    more.sort(key=lambda x: (x.get("name") or "").casefold())

    recent = _merge_recent(
        _pairs_as_recent(session, recent_pairs or []),
        _recent_assignments(
            session,
            profile_id=pid,
            txn_ids=recent_txn_ids,
            rule_id=recent_rule_id,
        ),
    )

    pct = 100.0 if total_exp == 0 else round(100.0 * categorized / total_exp, 1)
    return {
        "profile_id": pid,
        "expense_count": int(total_exp),
        "uncategorized_count": int(total_uncat),
        "categorized_count": categorized,
        "categorized_pct": pct,
        "confirm_queue": queue,
        "confirm_cap": confirm_cap,
        "category_chips": chips,
        "more_chips": more,
        "recent_assignments": recent,
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
    reassign: bool = False,
) -> dict[str, Any]:
    """Assign category to all matching uncategorized txns; optional rule."""
    cat = session.get(Category, category_id)
    if not cat:
        raise ValueError("Category not found")

    pid = _profile_id(session, profile_id)
    updated = 0
    applied_ids: list[int] = []
    sample: Transaction | None = None

    if transaction_ids:
        for tid in transaction_ids:
            t = session.get(Transaction, tid)
            if not t:
                continue
            if t.category_id is not None and not reassign:
                continue
            bound = _bind_category_to_txn(session, cat, t)
            t.category_id = bound.id
            t.confidence = Decimal("1")
            _sync_transfer_flag(t, bound, session)
            updated += 1
            applied_ids.append(t.id)
            sample = sample or t
            cat = bound
    elif payee_key:
        key = categorize_payee_key(payee_key) or payee_key.strip().lower()
        q = session.query(Transaction).filter(Transaction.status != "void")
        if not reassign:
            q = q.filter(Transaction.category_id.is_(None))
        if pid is not None:
            q = q.filter(Transaction.profile_id == pid)
        for t in q.limit(500).all():
            nk = categorize_payee_key(t.payee) or (t.payee or "").strip().lower()
            if nk != key:
                continue
            bound = _bind_category_to_txn(session, cat, t)
            if pid is not None and t.profile_id != pid and (bound.scope or "") != "business":
                continue
            t.category_id = bound.id
            t.confidence = Decimal("1")
            _sync_transfer_flag(t, bound, session)
            updated += 1
            applied_ids.append(t.id)
            sample = sample or t
            cat = bound
    else:
        raise ValueError("payee_key or transaction_ids required")

    rule_id = None
    if create_rule and sample is not None and not is_check_payee(sample.payee):
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
        "payee": (sample.payee if sample else payee_key) or "",
        "transaction_ids": applied_ids,
        "status": categorize_status(
            session,
            profile_id=pid,
            recent_txn_ids=applied_ids,
            recent_rule_id=rule_id,
        ),
    }


def undo_payee_category(
    session: Session,
    *,
    transaction_ids: list[int],
    rule_id: int | None = None,
    payee_key: str | None = None,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Clear the category on those transactions and drop the learned rule."""
    from honestspend.db import CategoryRule

    cleared = 0
    for tid in transaction_ids:
        t = session.get(Transaction, tid)
        if not t:
            continue
        t.category_id = None
        t.confidence = None
        t.is_transfer = False
        pair_id = t.transfer_pair_id
        t.transfer_pair_id = None
        if pair_id:
            pair = session.get(Transaction, pair_id)
            if pair:
                pair.transfer_pair_id = None
                pair.is_transfer = False
                pair.category_id = None
                _mark_skip_auto_link(pair)
            _mark_skip_auto_link(t)
        _restore_orig_profile(t)
        cleared += 1
    if rule_id:
        rule = session.get(CategoryRule, rule_id)
        if rule and rule.source == "learned":
            session.delete(rule)
    if payee_key:
        key = categorize_payee_key(payee_key)
        for rule in (
            session.query(CategoryRule)
            .filter(CategoryRule.source == "learned", CategoryRule.active.is_(True))
            .all()
        ):
            if categorize_payee_key(rule.pattern) == key:
                session.delete(rule)
    session.flush()
    return {
        "ok": True,
        "cleared": cleared,
        "status": categorize_status(session, profile_id=_profile_id(session, profile_id)),
    }


def _pairs_as_recent(session: Session, pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for p in pairs:
        payee = (p.get("out_payee") or p.get("in_payee") or "")[:80]
        key = (
            normalize_payee(payee) or payee.lower()[:40],
            str(p.get("from_account") or ""),
            str(p.get("to_account") or ""),
        )
        if key not in grouped:
            tid = p.get("out_txn_id") or p.get("in_txn_id")
            t = session.get(Transaction, tid) if tid else None
            cat = t.category if t and t.category_id else None
            grouped[key] = {
                "payee_key": key[0],
                "payee": payee or key[0],
                "category_id": int(cat.id) if cat else 0,
                "category_name": cat.display_name if cat else "Transfer",
                "count": 0,
                "transaction_ids": [],
                "from_account": p.get("from_account"),
                "to_account": p.get("to_account"),
            }
            order.append(key)
        g = grouped[key]
        g["count"] += 1
        for i in (p.get("out_txn_id"), p.get("in_txn_id")):
            if i and i not in g["transaction_ids"]:
                g["transaction_ids"].append(int(i))
    return [grouped[k] for k in order]


def _merge_recent(head: list[dict[str, Any]], tail: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in head + tail:
        key = (
            str(row.get("payee_key") or ""),
            str(row.get("from_account") or ""),
            str(row.get("to_account") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= 20:
            break
    return out


_SKIP_AUTO_LINK = "[skip-auto-link]"
_ORIG_PROF = re.compile(r"\[orig-profile:(\d+)\]")


def _mark_skip_auto_link(t: Transaction) -> None:
    memo = t.memo or ""
    if _SKIP_AUTO_LINK not in memo:
        t.memo = f"{memo} {_SKIP_AUTO_LINK}".strip()


def _clear_skip_auto_link(t: Transaction) -> None:
    if t.memo and _SKIP_AUTO_LINK in t.memo:
        t.memo = t.memo.replace(_SKIP_AUTO_LINK, "").strip() or None


def _restore_orig_profile(t: Transaction) -> None:
    m = _ORIG_PROF.search(t.memo or "")
    if not m:
        return
    t.profile_id = int(m.group(1))
    t.memo = _ORIG_PROF.sub("", t.memo or "").strip() or None


def _bind_category_to_txn(session: Session, cat: Category, t: Transaction) -> Category:
    """If the user picks a business category, move the txn onto that entity."""
    if (cat.scope or "") == "business":
        if t.profile_id != cat.profile_id and cat.profile_id is not None:
            if not _ORIG_PROF.search(t.memo or ""):
                t.memo = f"{t.memo or ''} [orig-profile:{t.profile_id}]".strip()
            t.profile_id = cat.profile_id
        return cat
    _restore_orig_profile(t)
    if cat.profile_id is None or cat.profile_id == t.profile_id:
        return cat
    alt = (
        session.query(Category)
        .filter(Category.profile_id == t.profile_id, Category.display_name == cat.display_name)
        .first()
    )
    return alt or cat


def _sync_transfer_flag(t: Transaction, cat: Category, session: Session) -> None:
    code = (cat.code or "").upper()
    is_xfer = code.startswith("SYS_TRANSFER") or code.startswith("SYS_CC")
    if is_xfer:
        t.is_transfer = True
        _clear_skip_auto_link(t)
        return
    t.is_transfer = False
    pair_id = t.transfer_pair_id
    t.transfer_pair_id = None
    if pair_id:
        pair = session.get(Transaction, pair_id)
        if pair:
            pair.transfer_pair_id = None
            if (pair.category and (pair.category.code or "").upper().startswith("SYS_")) or pair.is_transfer:
                pair.is_transfer = False


def _recent_assignments(
    session: Session,
    *,
    profile_id: int | None,
    limit: int = 20,
    txn_ids: list[int] | None = None,
    rule_id: int | None = None,
) -> list[dict[str, Any]]:
    if not txn_ids:
        return []
    q = session.query(Transaction).filter(
        Transaction.id.in_(txn_ids),
        Transaction.status != "void",
        Transaction.amount < 0,
    )
    rows = q.order_by(Transaction.id.desc()).limit(400).all()
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    for t in rows:
        key = categorize_payee_key(t.payee)
        cid = int(t.category_id or 0)
        gk = (key, cid)
        if gk not in grouped:
            cat = session.get(Category, cid)
            from_acct = t.account.nickname if t.account else ""
            to_acct = None
            if t.transfer_pair_id:
                pair = session.get(Transaction, t.transfer_pair_id)
                if pair and pair.account:
                    to_acct = pair.account.nickname
            if not to_acct:
                to_acct = _infer_counterparty(
                    session,
                    t.payee or "",
                    exclude_ids={t.account_id} if t.account_id else set(),
                )
            grouped[gk] = {
                "payee_key": key,
                "payee": (t.payee or key)[:80],
                "category_id": cid,
                "category_name": cat.display_name if cat else "Category",
                "count": 0,
                "transaction_ids": [],
                "from_account": from_acct,
                "to_account": to_acct,
                "rule_id": rule_id,
            }
            order.append(gk)
        grouped[gk]["count"] += 1
        ids: list[int] = grouped[gk]["transaction_ids"]
        if t.id not in ids:
            ids.append(t.id)
    out = [grouped[k] for k in order[:limit]]
    return out
