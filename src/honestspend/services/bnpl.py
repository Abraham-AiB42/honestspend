"""Buy-now-pay-later activity → finite future charges.

Klarna / Affirm / Afterpay / PayPal Pay in 4 / Zip / Sezzle (and siblings)
post one installment at a time. Remaining payments are scheduled bills with
an end date — Coming up and cash runway see them. Posted installments stay
as spend (not transfers, not voided).

Issuer “split this purchase” tables (Plan It, Flex Pay, Pay over Time) are
already on the card; those stay purchase_plan via promo_statement_parse.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, ScheduledItem, Transaction

ZERO = Decimal("0")
CENT = Decimal("0.01")

# Brand first so Zip never matches a ZIP code. PayPal only with Pay in 4 / Inst.
_BRANDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bklarna\b", re.I), "Klarna"),
    (re.compile(r"\baffirm\b", re.I), "Affirm"),
    (re.compile(r"\bafterpay\b", re.I), "Afterpay"),
    (re.compile(r"\bsezzle\b", re.I), "Sezzle"),
    (re.compile(r"\bquadpay\b", re.I), "Zip"),
    (re.compile(r"\bzip(?:\s*co|\s*pay|pay|\s*\*)", re.I), "Zip"),
    (
        re.compile(
            r"paypal.{0,24}(?:pay[\s\-]*in[\s\-]*4|payin4)|"
            r"pay[\s\-]*in[\s\-]*4|"
            r"payin4|"
            r"pp\*?\s*payin4|"
            r"paypal\s+inst(?:allment)?s?\b",
            re.I,
        ),
        "PayPal Pay in 4",
    ),
]

_N_OF_M = re.compile(
    r"(?:payment\s+)?(?P<n>\d+)\s*(?:of|/)\s*(?P<m>\d+)\b",
    re.I,
)
_STAR_MERCHANT = re.compile(r"\*\s*([A-Za-z][A-Za-z0-9&.' ]{1,40})")
_MONTHLY_HINT = re.compile(r"\bmonth(?:ly|s)?\b", re.I)
_BNPL_NOTE = re.compile(r"^bnpl:", re.I)


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT)


def _money(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        s = str(raw).replace(",", "").replace("$", "").strip()
        if not s:
            return None
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if d == 0:
        return None
    return _q(abs(d))


def bnpl_brand(payee: str | None) -> str | None:
    """Display brand if this payee is a BNPL lender, else None."""
    text = (payee or "").strip()
    if not text:
        return None
    for rx, name in _BRANDS:
        if rx.search(text):
            return name
    return None


def is_bnpl_payee(payee: str | None) -> bool:
    return bnpl_brand(payee) is not None


def _merchant_tail(payee: str, brand: str) -> str:
    m = _STAR_MERCHANT.search(payee or "")
    if m:
        tail = " ".join(m.group(1).split()).strip(" -.*")
        tail = re.sub(r"\b\d+\s*(?:of|/)\s*\d+\b", "", tail, flags=re.I).strip()
        if tail and tail.lower() not in brand.lower():
            return tail[:40]
    return ""


def _cadence(payee: str, total: int | None) -> str:
    if _MONTHLY_HINT.search(payee or ""):
        return "monthly"
    if total is not None and total >= 6:
        return "monthly"
    return "biweekly"


def _add_cadence(d: date, cadence: str, steps: int = 1) -> date:
    if steps <= 0:
        return d
    c = (cadence or "biweekly").lower()
    cur = d
    for _ in range(steps):
        if c == "weekly":
            cur = cur + timedelta(days=7)
        elif c == "monthly":
            y = cur.year + (cur.month // 12)
            m = cur.month % 12 + 1
            cur = date(y, m, min(cur.day, monthrange(y, m)[1]))
        else:
            cur = cur + timedelta(days=14)
    return cur


def parse_bnpl_payee(
    payee: str,
    amount: Decimal | None = None,
    *,
    txn_date: date | None = None,
) -> dict[str, Any] | None:
    """One activity line. remaining_after is payments still to post (excludes this one)."""
    brand = bnpl_brand(payee)
    if not brand:
        return None
    monthly = _money(amount)
    n = m = None
    hit = _N_OF_M.search(payee or "")
    if hit:
        try:
            n = int(hit.group("n"))
            m = int(hit.group("m"))
        except (TypeError, ValueError):
            n = m = None
        if n is None or m is None or n < 1 or m < 2 or n > m:
            n = m = None
    merchant = _merchant_tail(payee or "", brand)
    remaining_after = (m - n) if n is not None and m is not None else None
    cadence = _cadence(payee or "", m)
    name = f"{brand} · {merchant}" if merchant else brand
    if m is not None:
        name = f"{name} · {m} pay" if merchant else f"{brand} · {m} pay"
    return {
        "kind": "bnpl",
        "brand": brand,
        "name": name[:128],
        "monthly_payment": monthly,
        "payment_number": n,
        "total_payments": m,
        "remaining_after": remaining_after,
        "cadence": cadence,
        "txn_date": txn_date,
        "merchant": merchant or None,
    }


def extract_bnpl_from_text(text: str) -> list[dict[str, Any]]:
    """Statement / CSV lines that name a BNPL brand + amount + optional N of M."""
    if not text or not str(text).strip():
        return []
    hits: list[dict[str, Any]] = []
    money_rx = re.compile(r"[\d,]+\.\d{2}")
    for line in str(text).replace("\xa0", " ").splitlines():
        line = " ".join(line.split())
        if not line or not bnpl_brand(line):
            continue
        found = list(money_rx.finditer(line))
        amt = _money(found[-1].group(0)) if found else None
        row = parse_bnpl_payee(line, amt)
        if row is not None:
            hits.append(row)
    return _group_bnpl_hits(hits)


def extract_bnpl_from_payee_amounts(
    rows: list[tuple[str, Decimal | None, date | None]],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for payee, amount, txn_date in rows:
        row = parse_bnpl_payee(payee or "", amount, txn_date=txn_date)
        if row is not None:
            hits.append(row)
    return _group_bnpl_hits(hits)


def _group_bnpl_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Latest N-of-M per (brand, monthly, term). Same-day twins stay separate names."""
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}
    for h in hits:
        monthly = h.get("monthly_payment")
        key = (h.get("brand"), monthly, h.get("total_payments"), h.get("merchant") or "")
        cur = latest.get(key)
        n = h.get("payment_number") or 0
        if cur is None or n >= (cur.get("payment_number") or 0):
            latest[key] = dict(h)
    return list(latest.values())


def _note_key(brand: str, monthly: Decimal, total: int | None, merchant: str | None) -> str:
    merch = (merchant or "").strip().lower()[:24]
    tot = int(total) if total else 0
    return f"bnpl:{brand.lower()}:{monthly}:{tot}:{merch}"


def apply_bnpl_for_account(
    session: Session,
    account_id: int,
    text: str = "",
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Upsert finite ScheduledItem rows for remaining BNPL installments."""
    as_of = as_of or date.today()
    acct = session.get(Account, account_id)
    if not acct:
        return {"created": 0, "updated": 0, "ended": 0, "schedules": []}

    txn_rows = (
        session.query(Transaction.payee, Transaction.amount, Transaction.txn_date)
        .filter(
            Transaction.account_id == int(account_id),
            Transaction.status != "void",
        )
        .all()
    )
    terms = _group_bnpl_hits(
        extract_bnpl_from_text(text or "")
        + extract_bnpl_from_payee_amounts(
            [(p or "", a, d) for p, a, d in txn_rows]
        )
    )
    created = updated = ended = 0
    schedules: list[ScheduledItem] = []
    for term in terms:
        monthly = term.get("monthly_payment")
        remaining = term.get("remaining_after")
        if monthly is None or monthly <= ZERO:
            continue
        if remaining is None:
            continue
        brand = term["brand"]
        key = _note_key(brand, monthly, term.get("total_payments"), term.get("merchant"))
        existing = _find_bnpl_schedule(session, int(acct.profile_id), int(acct.id), key)
        if remaining <= 0:
            if existing and existing.active:
                existing.active = False
                existing.end_date = as_of
                existing.ended_reason = "BNPL plan finished"
                ended += 1
            continue
        last = term.get("txn_date") or as_of
        cadence = term.get("cadence") or "biweekly"
        nxt = _add_cadence(last, cadence, 1)
        end = _add_cadence(nxt, cadence, remaining - 1)
        name = (term.get("name") or brand)[:128]
        notes = f"{key} · {remaining} left"
        if existing:
            existing.name = name
            existing.amount = -monthly
            existing.cadence = cadence
            existing.next_date = nxt
            existing.end_date = end
            existing.notes = notes
            existing.active = True
            existing.ended_at = None
            existing.ended_reason = None
            existing.account_id = int(acct.id)
            updated += 1
            schedules.append(existing)
        else:
            row = ScheduledItem(
                profile_id=int(acct.profile_id),
                account_id=int(acct.id),
                name=name,
                amount=-monthly,
                next_date=nxt,
                start_date=nxt,
                end_date=end,
                cadence=cadence,
                certainty="fixed",
                kind="expense",
                notes=notes,
                active=True,
            )
            session.add(row)
            session.flush()
            created += 1
            schedules.append(row)
    if created or updated or ended:
        session.flush()
    return {
        "created": created,
        "updated": updated,
        "ended": ended,
        "schedules": schedules,
        "found": len(terms),
    }


def apply_bnpl_for_profile(
    session: Session,
    *,
    profile_id: int | None,
    as_of: date | None = None,
) -> dict[str, Any]:
    q = session.query(Account)
    if profile_id is not None:
        q = q.filter(Account.profile_id == int(profile_id))
    if hasattr(Account, "archived_at"):
        q = q.filter(Account.archived_at.is_(None))
    created = updated = ended = 0
    for acct in q.all():
        try:
            out = apply_bnpl_for_account(session, int(acct.id), "", as_of=as_of)
        except Exception:
            continue
        created += int(out.get("created") or 0)
        updated += int(out.get("updated") or 0)
        ended += int(out.get("ended") or 0)
    return {"created": created, "updated": updated, "ended": ended}


def _find_bnpl_schedule(
    session: Session,
    profile_id: int,
    account_id: int,
    key: str,
) -> ScheduledItem | None:
    rows = (
        session.query(ScheduledItem)
        .filter(
            ScheduledItem.profile_id == profile_id,
            ScheduledItem.account_id == account_id,
        )
        .order_by(ScheduledItem.id.desc())
        .all()
    )
    for row in rows:
        notes = row.notes or ""
        if notes.startswith(key) or notes.startswith(key + " "):
            return row
    return None


def is_bnpl_schedule(item: ScheduledItem) -> bool:
    return bool(_BNPL_NOTE.search(item.notes or ""))


# ---------------------------------------------------------------------------
# Can I buy? quotes — typical US consumer terms, overridable at checkout
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BnplProvider:
    key: str
    name: str
    default_plan: str  # pay_in_4 | monthly
    payments: int
    cadence: str
    apr: Decimal
    origination_fee: Decimal
    service_fee: Decimal
    late_fee: Decimal
    note: str


BNPL_PROVIDERS: dict[str, BnplProvider] = {
    "klarna": BnplProvider(
        "klarna",
        "Klarna",
        "pay_in_4",
        4,
        "biweekly",
        ZERO,
        ZERO,
        ZERO,
        Decimal("7.00"),
        "Pay in 4 is 0% if on time. Typical US late fee about $7 (not added unless you miss).",
    ),
    "afterpay": BnplProvider(
        "afterpay",
        "Afterpay",
        "pay_in_4",
        4,
        "biweekly",
        ZERO,
        ZERO,
        ZERO,
        Decimal("8.00"),
        "Pay in 4 is 0% if on time. Typical late fee about $8 (not added unless you miss).",
    ),
    "sezzle": BnplProvider(
        "sezzle",
        "Sezzle",
        "pay_in_4",
        4,
        "biweekly",
        ZERO,
        ZERO,
        ZERO,
        Decimal("10.00"),
        "Pay in 4 is 0% if on time. Some checkouts add a small service fee; typical late fee about $10.",
    ),
    "zip": BnplProvider(
        "zip",
        "Zip",
        "pay_in_4",
        4,
        "biweekly",
        ZERO,
        ZERO,
        ZERO,
        Decimal("8.00"),
        "Pay in 4 is 0% if on time. Typical late fee about $8 (not added unless you miss).",
    ),
    "paypal_pay_in_4": BnplProvider(
        "paypal_pay_in_4",
        "PayPal Pay in 4",
        "pay_in_4",
        4,
        "biweekly",
        ZERO,
        ZERO,
        ZERO,
        ZERO,
        "Pay in 4 is 0% if on time. PayPal often has no late fee; missed payments can still hurt.",
    ),
    "affirm": BnplProvider(
        "affirm",
        "Affirm",
        "monthly",
        12,
        "monthly",
        ZERO,
        ZERO,
        ZERO,
        ZERO,
        "Enter the APR from checkout (often 0–36%). Affirm usually has no late fee; interest is the cost.",
    ),
}


def list_bnpl_providers() -> list[dict[str, Any]]:
    return [
        {
            "key": p.key,
            "name": p.name,
            "default_plan": p.default_plan,
            "payments": p.payments,
            "cadence": p.cadence,
            "late_fee": str(p.late_fee),
            "note": p.note,
        }
        for p in BNPL_PROVIDERS.values()
    ]


def resolve_bnpl_provider(raw: str | None) -> BnplProvider:
    key = (raw or "klarna").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "paypal": "paypal_pay_in_4",
        "pay_in_4": "paypal_pay_in_4",
        "paypal_payin4": "paypal_pay_in_4",
        "quadpay": "zip",
    }
    key = aliases.get(key, key)
    return BNPL_PROVIDERS.get(key, BNPL_PROVIDERS["klarna"])


def _split_installments(total: Decimal, n: int) -> list[Decimal]:
    n = max(2, int(n))
    total = _q(total)
    base = (total / n).quantize(CENT, rounding=ROUND_HALF_UP)
    last = _q(total - base * (n - 1))
    if last <= ZERO:
        base = (total / n).quantize(CENT, rounding=ROUND_DOWN)
        last = _q(total - base * (n - 1))
    return [base] * (n - 1) + [last]


def _amortized_payment(principal: Decimal, apr_pct: Decimal, n: int) -> Decimal:
    n = max(2, int(n))
    r = (_q(apr_pct) / Decimal("100")) / Decimal("12")
    if r <= ZERO:
        return _split_installments(principal, n)[0]
    grow = (Decimal("1") + r) ** n
    return (principal * r * grow / (grow - Decimal("1"))).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def quote_bnpl(
    amount: Decimal | Any,
    *,
    provider: str | None = "klarna",
    plan: str | None = None,
    payments: int | None = None,
    apr: Decimal | Any | None = None,
    origination_fee: Decimal | Any | None = None,
    service_fee: Decimal | Any | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Installment quote for Can I buy. Late fees are disclosed, not assumed."""
    as_of = as_of or date.today()
    prov = resolve_bnpl_provider(provider)
    sticker = _q(_money(amount) or ZERO)
    if sticker <= ZERO:
        raise ValueError("amount must be positive")
    plan_s = (plan or prov.default_plan).strip().lower().replace("-", "_")
    if plan_s in ("payin4", "pay in 4", "4pay"):
        plan_s = "pay_in_4"
    if plan_s not in ("pay_in_4", "monthly"):
        plan_s = prov.default_plan
    n = int(payments) if payments not in (None, "") else (
        4 if plan_s == "pay_in_4" else int(prov.payments)
    )
    n = max(2, min(36, n))
    if plan_s == "pay_in_4":
        n = 4
    cadence = "biweekly" if plan_s == "pay_in_4" else "monthly"
    apr_d = _q(_money(apr) or prov.apr) if apr not in (None, "") else _q(prov.apr)
    # apr may be 9.99 (percent), _money treats it as dollars-like — use Decimal directly
    if apr not in (None, ""):
        try:
            apr_d = Decimal(str(apr).replace("%", "").strip())
            if apr_d < 0:
                apr_d = ZERO
        except (InvalidOperation, ValueError):
            apr_d = _q(prov.apr)
    orig = _q(_money(origination_fee) or ZERO) if origination_fee not in (None, "") else _q(
        prov.origination_fee
    )
    svc = _q(_money(service_fee) or ZERO) if service_fee not in (None, "") else _q(
        prov.service_fee
    )
    fees = _q(orig + svc)
    principal = _q(sticker + fees)
    if plan_s == "monthly" and apr_d > ZERO:
        installment = _amortized_payment(principal, apr_d, n)
        parts = [installment] * (n - 1)
        last = _q(installment)  # level payment; total is n * installment
        parts.append(last)
        total_paid = _q(installment * n)
    else:
        parts = _split_installments(principal, n)
        installment = parts[0]
        total_paid = _q(sum(parts, ZERO))
    first = parts[0]
    remaining_total = _q(total_paid - first)
    finance = _q(total_paid - sticker)
    nxt = _add_cadence(as_of, cadence, 1)
    end = _add_cadence(nxt, cadence, n - 2) if n > 1 else as_of
    return {
        "provider": prov.key,
        "provider_name": prov.name,
        "plan": plan_s,
        "payments": n,
        "cadence": cadence,
        "amount": str(sticker),
        "origination_fee": str(orig),
        "service_fee": str(svc),
        "fees": str(fees),
        "apr": str(apr_d),
        "principal": str(principal),
        "installment": str(installment),
        "first_due": str(first),
        "remaining_total": str(remaining_total),
        "remaining_count": n - 1,
        "total_cost": str(total_paid),
        "finance_charge": str(finance),
        "extra_cost": str(finance),
        "late_fee_typical": str(prov.late_fee),
        "late_fee_included": False,
        "next_date": nxt.isoformat(),
        "end_date": end.isoformat(),
        "note": prov.note,
    }


def existing_bnpl_burden(
    session: Session,
    *,
    profile_id: int | None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Open BNPL schedules still on the books (imported or committed)."""
    as_of = as_of or date.today()
    from honestspend.engine.schedule_expand import expand_scheduled

    q = session.query(ScheduledItem).filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        q = q.filter(ScheduledItem.profile_id == int(profile_id))
    items: list[dict[str, Any]] = []
    remaining = ZERO
    next_date: date | None = None
    horizon = as_of + timedelta(days=400)
    for s in q.all():
        if not is_bnpl_schedule(s):
            continue
        try:
            stored = Decimal(str(s.amount or 0))
        except (InvalidOperation, ValueError):
            stored = ZERO
        occ = expand_scheduled(
            item_id=s.id,
            name=s.name or "BNPL",
            amount=stored,
            next_date=s.next_date,
            cadence=s.cadence or "biweekly",
            certainty=s.certainty or "fixed",
            as_of=as_of,
            horizon_end=horizon,
            end_date=getattr(s, "end_date", None),
            start_date=getattr(s, "start_date", None),
            active=bool(s.active),
        )
        due = ZERO
        first: date | None = None
        for ev in occ:
            due += abs(_q(Decimal(str(ev.amount))))
            if first is None or ev.on_date < first:
                first = ev.on_date
        if due <= ZERO:
            continue
        remaining += due
        if first and (next_date is None or first < next_date):
            next_date = first
        items.append(
            {
                "name": s.name,
                "account_id": s.account_id,
                "remaining": str(_q(due)),
                "next_date": first.isoformat() if first else None,
                "cadence": s.cadence,
            }
        )
    return {
        "count": len(items),
        "remaining_total": str(_q(remaining)),
        "next_date": next_date.isoformat() if next_date else None,
        "items": items,
    }


def schedule_bnpl_quote(
    session: Session,
    *,
    account_id: int,
    quote: dict[str, Any],
    as_of: date | None = None,
    payee: str | None = None,
) -> ScheduledItem | None:
    """Book remaining installments after the first payment is posted."""
    as_of = as_of or date.today()
    acct = session.get(Account, account_id)
    if not acct:
        return None
    left = int(quote.get("remaining_count") or 0)
    monthly = _money(quote.get("installment"))
    if left < 1 or monthly is None or monthly <= ZERO:
        return None
    brand = str(quote.get("provider_name") or quote.get("provider") or "BNPL")
    n = int(quote.get("payments") or left + 1)
    cadence = str(quote.get("cadence") or "biweekly")
    nxt = date.fromisoformat(str(quote["next_date"])) if quote.get("next_date") else _add_cadence(
        as_of, cadence, 1
    )
    end = date.fromisoformat(str(quote["end_date"])) if quote.get("end_date") else _add_cadence(
        nxt, cadence, left - 1
    )
    tail = (payee or "").strip()
    name = f"{brand} · {n} pay"
    if tail and tail.lower() not in name.lower():
        name = f"{brand} · {tail}"[:128]
    key = _note_key(brand, monthly, n, tail or None)
    notes = f"{key} · {left} left"
    existing = _find_bnpl_schedule(session, int(acct.profile_id), int(acct.id), key)
    if existing:
        existing.name = name[:128]
        existing.amount = -monthly
        existing.cadence = cadence
        existing.next_date = nxt
        existing.end_date = end
        existing.notes = notes
        existing.active = True
        existing.ended_at = None
        existing.ended_reason = None
        session.flush()
        return existing
    row = ScheduledItem(
        profile_id=int(acct.profile_id),
        account_id=int(acct.id),
        name=name[:128],
        amount=-monthly,
        next_date=nxt,
        start_date=nxt,
        end_date=end,
        cadence=cadence,
        certainty="fixed",
        kind="expense",
        notes=notes,
        active=True,
    )
    session.add(row)
    session.flush()
    return row

