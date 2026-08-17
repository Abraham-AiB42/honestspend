"""Three-step setup budget plan: income → recurring bills → variable envelopes."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.data.budget_playbook import (
    BLS_NOTE,
    INCOME_PATTERNS,
    PAYROLL_PROCESSORS,
    RECURRING_KINDS,
    VARIABLE_ENVELOPES,
)
from honestspend.db import Account, Category, Profile, ScheduledItem, Transaction
from honestspend.services.budget_service import apply_vendor_budget_policy, list_rules
from honestspend.services.recurring_detect import (
    _guess_cadence,
    accept_recurring_suggestion,
    detect_recurring,
    vendor_budget_key,
)
from honestspend.services.setup_budgets import _d, _money, _pid, budgets_review

ZERO = Decimal("0")


def _rx(pats: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in pats), re.I)


_INCOME_RX = _rx(INCOME_PATTERNS)
_KIND_RX = [(k, _rx(tuple(k["patterns"]))) for k in RECURRING_KINDS]
_PROCESSOR_RX = _rx(tuple(rf"\b{re.escape(p)}\b" for p in PAYROLL_PROCESSORS))
_INCOME_NOISE_RX = re.compile(
    r"\b(direct\s*dep(?:osit)?|dir\s*dep|payroll|salary|paycheck|ppd|ccd|ach|"
    r"trace|des|indn|co id|id|company)\b|[:*#]",
    re.I,
)
_SKIP_INCOME_RX = re.compile(
    r"\b(refund|rebate|reimburse|cashback|cash back|transfer|zelle|venmo|paypal|"
    r"atm|deposit\s*correction|void)\b",
    re.I,
)


def employer_from_payee(payee: str | None) -> str | None:
    """Personal deposits show the employer, not ADP/Gusto/Paychex."""
    raw = (payee or "").strip()
    if not raw:
        return None
    cleaned = _PROCESSOR_RX.sub(" ", raw)
    cleaned = _INCOME_NOISE_RX.sub(" ", cleaned)
    cleaned = re.sub(r"[^a-zA-Z0-9&.'\-\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -.")
    if len(cleaned) < 3:
        return None
    return cleaned[:128]


def _is_business_book(session: Session, profile_id: int) -> bool:
    p = session.get(Profile, int(profile_id))
    if not p:
        return False
    et = (p.entity_type or "").strip().lower()
    if et in ("business", "s_corp", "llc", "partnership", "c_corp"):
        return True
    return (p.tax_form_primary or "").upper() in ("1120S", "1065", "SCHC", "1120")


def match_recurring_kind(text: str) -> dict[str, Any] | None:
    blob = text or ""
    for kind, rx in _KIND_RX:
        if rx.search(blob):
            return kind
    return None


def match_envelope(cat: Category | None, payee: str = "") -> dict[str, Any] | None:
    blob = f"{(cat.display_name if cat else '')} {(cat.budget_group if cat else '')} {payee}".lower()
    bg = ((cat.budget_group if cat else "") or "").lower()
    for env in VARIABLE_ENVELOPES:
        groups = tuple(env.get("budget_groups") or ())
        hints = tuple(env.get("name_hints") or ())
        if bg == "food":
            if any(h in blob for h in hints):
                return env
            continue
        if (bg and bg in groups) or any(h in blob for h in hints):
            return env
    if bg == "food":
        return next((e for e in VARIABLE_ENVELOPES if e["id"] == "groceries"), None)
    return None


def plan_meta() -> dict[str, Any]:
    return {
        "steps": [
            {"id": "income", "title": "Income", "blurb": "Paychecks and other deposits that refill Safe to spend."},
            {
                "id": "bills",
                "title": "Recurring bills",
                "blurb": "Mortgage, rent, cell, internet, utilities, loans — confirm or send to a variable budget.",
            },
            {
                "id": "envelopes",
                "title": "Variable budgets",
                "blurb": "Groceries, dining, fuel, shopping — the amounts that change.",
            },
        ],
        "bls_note": BLS_NOTE,
        "envelope_catalog": [
            {
                "id": e["id"],
                "label": e["label"],
                "period": e["period"],
                "subcategories": list(e.get("subcategories") or []),
            }
            for e in VARIABLE_ENVELOPES
        ],
        "bill_catalog": [
            {"id": k["id"], "label": k["label"], "group": k["group"]} for k in RECURRING_KINDS
        ],
    }


def income_review(session: Session, *, profile_id: int | None = None) -> dict[str, Any]:
    pid = _pid(session, profile_id)
    as_of = date.today()
    start = as_of - timedelta(days=200)
    existing = []
    if pid:
        for s in (
            session.query(ScheduledItem)
            .filter(
                ScheduledItem.profile_id == int(pid),
                ScheduledItem.active.is_(True),
                ScheduledItem.kind == "income",
            )
            .order_by(ScheduledItem.next_date)
            .all()
        ):
            existing.append(_sched_row(s, role="income", kind_label="Income"))

    detected = []
    if pid:
        business = _is_business_book(session, int(pid))
        cash_ids = {
            int(a.id)
            for a in session.query(Account)
            .filter(
                Account.profile_id == int(pid),
                Account.kind.in_(("checking", "savings", "cash")),
            )
            .all()
        }
        q = session.query(Transaction).filter(
            Transaction.profile_id == int(pid),
            Transaction.txn_date >= start,
            Transaction.txn_date <= as_of,
            Transaction.amount > 0,
            Transaction.is_transfer.is_(False),
            Transaction.status != "void",
        )
        if cash_ids:
            q = q.filter(Transaction.account_id.in_(cash_ids))
        by_key: dict[str, list[Transaction]] = defaultdict(list)
        for t in q.all():
            if _SKIP_INCOME_RX.search(t.payee or ""):
                continue
            # Business books: ADP/Gusto is you paying staff, not a deposit to label.
            if business and _PROCESSOR_RX.search(t.payee or ""):
                continue
            employer = employer_from_payee(t.payee)
            if employer is None:
                # Processor-only string (just "ADP PAYROLL") — not a personal paycheck name.
                continue
            key = vendor_budget_key(employer) or employer.lower()
            if len(key) < 3:
                continue
            by_key[key].append(t)
        known = {vendor_budget_key(r["name"]) for r in existing}
        for key, hits in by_key.items():
            if key in known:
                continue
            amounts = [abs(_d(t.amount)) for t in hits]
            dates = sorted({t.txn_date for t in hits if t.txn_date})
            blob = " ".join((t.payee or "") for t in hits[:4])
            keyword = bool(_INCOME_RX.search(blob))
            cadence = _guess_cadence(dates)
            if not keyword and not (
                len(hits) >= 2
                and cadence in ("weekly", "biweekly", "monthly")
                and min(amounts) >= 200
            ):
                continue
            employers = [employer_from_payee(t.payee) for t in hits]
            employers = [e for e in employers if e]
            display = max(set(employers), key=employers.count) if employers else key
            last = dates[-1] if dates else as_of
            cad = cadence or "biweekly"
            delta = 14 if cad == "biweekly" else 7 if cad == "weekly" else 30
            detected.append(
                {
                    "id": None,
                    "name": display[:128],
                    "amount": str(max(amounts).quantize(Decimal("0.01"))),
                    "amount_display": _money(max(amounts)),
                    "cadence": cad,
                    "next_date": (last + timedelta(days=delta)).isoformat(),
                    "occurrences": len(hits),
                    "selected": True,
                    "source": "history",
                    "reason": "Employer deposit" if keyword else "Repeating deposit",
                }
            )
        detected.sort(key=lambda r: -_d(r["amount"]))

    monthly = ZERO
    for row in existing + detected:
        monthly += _to_monthly(_d(row["amount"]), row.get("cadence") or "monthly")
    return {
        **plan_meta(),
        "step": "income",
        "profile_id": pid,
        "existing": existing,
        "detected": detected,
        "monthly_total": str(monthly.quantize(Decimal("0.01"))),
        "monthly_total_display": _money(monthly),
        "message": "",
    }


def bills_review(session: Session, *, profile_id: int | None = None) -> dict[str, Any]:
    pid = _pid(session, profile_id)
    existing = []
    if pid:
        for s in (
            session.query(ScheduledItem)
            .filter(
                ScheduledItem.profile_id == int(pid),
                ScheduledItem.active.is_(True),
                ScheduledItem.kind == "expense",
            )
            .order_by(ScheduledItem.next_date)
            .all()
        ):
            kind = match_recurring_kind(f"{s.name} {s.vendor or ''}")
            existing.append(_sched_row(s, role="bill", kind_label=(kind or {}).get("label") or "Bill"))

    det = detect_recurring(session, profile_id=pid, lookback_days=200, min_occurrences=2, limit=20)
    detected = []
    known = {vendor_budget_key(r["name"]) for r in existing}
    # Playbook scan — includes "Payment to T-Mobile" (detect_recurring skips payment-to as card pay).
    if pid:
        start = date.today() - timedelta(days=200)
        rows = (
            session.query(Transaction)
            .filter(
                Transaction.profile_id == int(pid),
                Transaction.txn_date >= start,
                Transaction.amount < 0,
                Transaction.is_transfer.is_(False),
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
        for key, hits in by_key.items():
            if key in known:
                continue
            kind = match_recurring_kind(" ".join((t.payee or "") for t in hits[:6]))
            if not kind or len(hits) < 2:
                continue
            dates = sorted({t.txn_date for t in hits if t.txn_date})
            amounts = [abs(_d(t.amount)) for t in hits]
            names: dict[str, int] = defaultdict(int)
            for t in hits:
                names[(t.payee or key).strip()] += 1
            display = max(names.items(), key=lambda x: x[1])[0]
            cad = _guess_cadence(dates) or kind.get("cadence") or "monthly"
            last = dates[-1] if dates else date.today()
            detected.append(
                {
                    "id": None,
                    "name": display[:128],
                    "amount": str(max(amounts).quantize(Decimal("0.01"))),
                    "amount_display": _money(max(amounts)),
                    "cadence": cad,
                    "next_date": (last + timedelta(days=30)).isoformat(),
                    "occurrences": len(hits),
                    "selected": True,
                    "move_to_budget": False,
                    "kind_id": kind["id"],
                    "kind_label": kind["label"],
                    "group": kind["group"],
                    "account_id": hits[0].account_id,
                    "source": "playbook",
                    "reason": f"{kind['label']} · {len(hits)} charges in the books",
                }
            )
            known.add(key)
    for sug in det.get("suggestions") or []:
        key = vendor_budget_key(sug.get("name"))
        if key in known:
            continue
        kind = match_recurring_kind(str(sug.get("name") or ""))
        detected.append(
            {
                "id": None,
                "name": sug.get("name"),
                "amount": str(sug.get("amount_abs") or abs(_d(sug.get("amount")))),
                "amount_display": _money(sug.get("amount_abs") or abs(_d(sug.get("amount")))),
                "cadence": sug.get("cadence") or "monthly",
                "next_date": sug.get("suggested_next_date"),
                "occurrences": sug.get("occurrences"),
                "selected": kind is not None,
                "move_to_budget": False,
                "kind_id": (kind or {}).get("id"),
                "kind_label": (kind or {}).get("label") or "Other repeating",
                "group": (kind or {}).get("group") or "Other",
                "account_id": sug.get("suggested_account_id") or sug.get("account_id"),
                "source": "history",
                "reason": sug.get("reason"),
            }
        )

    # Playbook slots we did not find — still list so the user can add later.
    found_ids = {r.get("kind_id") for r in existing + detected if r.get("kind_id")}
    missing = [
        {"id": k["id"], "label": k["label"], "group": k["group"]}
        for k in RECURRING_KINDS
        if k["id"] not in found_ids
    ]
    monthly = ZERO
    for row in existing + [d for d in detected if d.get("selected")]:
        monthly += _to_monthly(_d(row["amount"]), row.get("cadence") or "monthly")
    return {
        **plan_meta(),
        "step": "bills",
        "profile_id": pid,
        "existing": existing,
        "detected": detected,
        "not_seen": missing,
        "monthly_total": str(monthly.quantize(Decimal("0.01"))),
        "monthly_total_display": _money(monthly),
        "message": "",
    }


def envelopes_review(session: Session, *, profile_id: int | None = None) -> dict[str, Any]:
    pid = _pid(session, profile_id)
    # Seed if empty, then drop envelopes that are really bills.
    rev = budgets_review(session, profile_id=pid, seed_if_empty=True)
    kept = []
    for rule in list_rules(session, pid):
        cat = session.get(Category, rule.category_id)
        blob = f"{rule.name or ''} {cat.display_name if cat else ''} {cat.budget_group if cat else ''}"
        if match_recurring_kind(blob):
            apply_vendor_budget_policy(session, rule)
            continue
        env = match_envelope(cat, rule.name or "")
        st = next((r for r in (rev.get("rules") or []) if r.get("id") == rule.id), None)
        if st is None:
            continue
        if env:
            st["envelope_id"] = env["id"]
            st["envelope_label"] = env["label"]
            st["subcategories"] = list(env.get("subcategories") or [])
            st["suggested_period"] = env["period"]
        kept.append(st)
    # Refresh after possible drops
    live_ids = {r.id for r in list_rules(session, pid)}
    kept = [k for k in kept if k.get("id") in live_ids]
    rev["rules"] = kept
    rev["count"] = len(kept)
    rev["step"] = "envelopes"
    rev.update(plan_meta())
    rev["message"] = ""
    return rev


def apply_plan(
    session: Session,
    *,
    step: str,
    updates: list[dict[str, Any]],
    profile_id: int | None = None,
) -> dict[str, Any]:
    pid = _pid(session, profile_id)
    step = (step or "").strip().lower()
    changed = []
    if step == "income":
        for u in updates:
            if u.get("delete") and u.get("id"):
                row = session.get(ScheduledItem, int(u["id"]))
                if row:
                    row.active = False
                    changed.append({"id": row.id, "dropped": True})
                continue
            if u.get("id") and "amount" in u:
                row = session.get(ScheduledItem, int(u["id"]))
                if row:
                    row.amount = abs(_d(u["amount"]))
                    if u.get("cadence"):
                        row.cadence = str(u["cadence"])
                    if u.get("next_date"):
                        row.next_date = date.fromisoformat(str(u["next_date"])[:10])
                    if u.get("name"):
                        row.name = str(u["name"])[:128]
                    changed.append({"id": row.id, "amount": str(row.amount)})
                continue
            if u.get("create") or u.get("selected"):
                amt = abs(_d(u.get("amount")))
                if amt <= 0:
                    continue
                nxt = u.get("next_date")
                nxt_d = date.fromisoformat(str(nxt)[:10]) if nxt else date.today() + timedelta(days=14)
                row = ScheduledItem(
                    profile_id=int(pid or 0),
                    name=(u.get("name") or "Income")[:128],
                    amount=amt,
                    cadence=str(u.get("cadence") or "biweekly"),
                    next_date=nxt_d,
                    kind="income",
                    certainty="expected",
                    notes="Confirmed on budget income step",
                    active=True,
                )
                session.add(row)
                session.flush()
                changed.append({"id": row.id, "created": True})
        session.flush()
        return {"ok": True, "changed": changed, "review": income_review(session, profile_id=pid)}

    if step == "bills":
        for u in updates:
            if u.get("move_to_budget"):
                if u.get("id"):
                    row = session.get(ScheduledItem, int(u["id"]))
                    if row:
                        row.active = False
                        changed.append({"id": row.id, "moved_to_budget": True})
                continue
            if u.get("delete") and u.get("id"):
                row = session.get(ScheduledItem, int(u["id"]))
                if row:
                    row.active = False
                    changed.append({"id": row.id, "dropped": True})
                continue
            if u.get("id") and "amount" in u:
                row = session.get(ScheduledItem, int(u["id"]))
                if row:
                    row.amount = -abs(_d(u["amount"]))
                    if u.get("cadence"):
                        row.cadence = str(u["cadence"])
                    if u.get("next_date"):
                        row.next_date = date.fromisoformat(str(u["next_date"])[:10])
                    changed.append({"id": row.id, "amount": str(row.amount)})
                continue
            if u.get("selected") or u.get("create"):
                if u.get("move_to_budget"):
                    continue
                amt = abs(_d(u.get("amount")))
                if amt <= 0 or not u.get("name"):
                    continue
                res = accept_recurring_suggestion(
                    session,
                    name=str(u["name"]),
                    amount=amt,
                    cadence=str(u.get("cadence") or "monthly"),
                    next_date=u.get("next_date"),
                    profile_id=pid,
                    account_id=u.get("account_id"),
                )
                changed.append(res)
        session.flush()
        return {"ok": True, "changed": changed, "review": bills_review(session, profile_id=pid)}

    from honestspend.services.setup_budgets import apply_budget_edits

    res = apply_budget_edits(session, updates=updates)
    res["review"] = envelopes_review(session, profile_id=pid)
    return res


def _to_monthly(amount: Decimal, cadence: str) -> Decimal:
    c = (cadence or "monthly").lower()
    if c == "weekly":
        return amount * Decimal("52") / Decimal("12")
    if c == "biweekly":
        return amount * Decimal("26") / Decimal("12")
    if c == "yearly":
        return amount / Decimal("12")
    return amount


def _sched_row(s: ScheduledItem, *, role: str, kind_label: str) -> dict[str, Any]:
    amt = abs(_d(s.amount))
    kind = match_recurring_kind(f"{s.name} {s.vendor or ''}")
    return {
        "id": s.id,
        "name": s.name,
        "amount": str(amt.quantize(Decimal("0.01"))),
        "amount_display": _money(amt),
        "cadence": s.cadence or "monthly",
        "next_date": s.next_date.isoformat() if s.next_date else None,
        "kind_id": (kind or {}).get("id"),
        "kind_label": kind_label,
        "group": (kind or {}).get("group") or "",
        "source": "scheduled",
        "role": role,
        "selected": True,
    }
