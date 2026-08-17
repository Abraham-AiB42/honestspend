"""Parse promo plan tables and intro periods from credit statement text.

Parse-only: returns term dicts for callers (import apply is separate).
Never invents rows from a lone "0%" without a balance or plan table.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")

# Header line for Amazon / retail Interest Saving Balance tables
# plus issuer “split this purchase” desks (Plan It, Flex Pay, Pay over Time).
_ISB_HEADER = re.compile(
    r"Interest\s+Saving\s+Balance|"
    r"Pay\s+Over\s+Time|"
    r"My\s+Chase\s+Plan|"
    r"Plan\s+It|"
    r"Flex\s+Pay|"
    r"Split\s+(?:this\s+)?purchase|"
    r"^\s*Plan\s+Remaining\s+Monthly\s+payment\s+Payments?\s+left\s*$",
    re.I | re.M,
)

# “4 payments of $37.50” / “Payment 2 of 4” on issuer or BNPL statements
_N_PAYMENTS_OF = re.compile(
    r"(?P<count>\d+)\s+payments?\s+of\s+\$?(?P<amt>[\d,]+\.\d{2})",
    re.I,
)
_PAYMENT_N_OF_M = re.compile(
    r"payments?\s+(?P<n>\d+)\s+of\s+(?P<m>\d+)",
    re.I,
)

# Plan row: name … $remaining … $monthly … payments_left
_ISB_ROW = re.compile(
    r"^(?P<name>.+?)\s+"
    r"\$?(?P<remaining>[\d,]+\.\d{2})\s+"
    r"\$?(?P<monthly>[\d,]+\.\d{2})\s+"
    r"(?P<left>\d+)\s*$",
    re.M,
)

# Intro / promotional APR with end date + balance subject to promo
_INTRO = re.compile(
    r"(?:promotional|promo|intro(?:ductory)?)\s*(?:apr|rate)?\s*"
    r"(?P<apr>[\d.]+)\s*%\s*"
    r"(?:through|until|ends?|thru|expir\w*)\s*"
    r"(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r"[^\n]{0,80}?"
    r"(?:balance\s*(?:subject\s*to\s*)?promo|promo(?:tional)?\s*balance|"
    r"amount\s*subject\s*to\s*promo)[:\s]*\$?\s*"
    r"(?P<bal>[\d,]+\.?\d*)",
    re.I,
)

# Alternate order: balance first, then 0% through date
_INTRO_BAL_FIRST = re.compile(
    r"(?:balance\s*(?:subject\s*to\s*)?promo|promo(?:tional)?\s*balance)"
    r"[:\s]*\$?\s*(?P<bal>[\d,]+\.?\d*)"
    r"[^\n]{0,80}?"
    r"(?:promotional|promo|intro(?:ductory)?)\s*(?:apr|rate)?\s*"
    r"(?P<apr>[\d.]+)\s*%\s*"
    r"(?:through|until|ends?|thru|expir\w*)\s*"
    r"(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.I,
)

# Home Depot / Citi deferred interest
_DEFERRED_PAYOFF = re.compile(
    r"promotional\s+balance\s+of\s+\$?\s*(?P<bal>[\d,]+\.\d{2})\s+"
    r"in\s+full\s+by\s+(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.I,
)

# Wells Fargo: PROMOTIONAL RATE EXPIRES 05/07/27 0.00% … $8,189.34
_WF_PROMO = re.compile(
    r"PROMOTIONAL\s+RATE\s+EXPIRES\s+(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
    r"(?P<apr>[\d.]+)\s*%"
    r"(?:[^\n$]*\$[\d,]+\.\d{2}){0,3}"
    r"[^\n$]*\$(?P<bal>[\d,]+\.\d{2})",
    re.I,
)

# Amex: Introductory Purchase / Rate Expires 03/22/2027 / 0.00% $6,997.17
_AMEX_INTRO = re.compile(
    r"Introductory\s+Purchase.{0,120}?"
    r"Rate\s+Expires\s+(?P<end>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r".{0,80}?(?P<apr>[\d.]+)\s*%\s+\$?(?P<bal>[\d,]+\.\d{2})",
    re.I | re.S,
)

# Chase Amazon / Prime Visa Equal Pay Promo 0.00% (d) 07/15/26 $164.45
_EQUAL_PAY = re.compile(
    r"Equal\s+Pay\s+Promo\s+(?P<apr>[\d.]+)\s*%[^\n$]{0,40}?"
    r"(?:(?P<end>\d{1,2}/\d{1,2}/\d{2,4})\s+)?"
    r"\$?(?P<bal>[\d,]+\.\d{2})",
    re.I,
)

# Chase Interest Saving Balance: $1,229.45  (pay this cycle to keep grace)
_ISB_LUMP = re.compile(
    r"Interest\s+Saving\s+Balance\s*:\s*\$?\s*(?P<bal>[\d,]+\.\d{2})",
    re.I,
)

# Apple Card / issuer activity: MONTHLY INSTALLMENTS (11 OF 24)
_INSTALLMENT_N_OF_M = re.compile(
    r"(?:monthly\s+installments?|installment)\s*\(\s*(?P<n>\d+)\s+of\s+(?P<m>\d+)\s*\)",
    re.I,
)
_MONEY_2 = re.compile(r"[\d,]+\.\d{2}")

# Pending BT offer with a rate + duration (not boilerplate "interest on balance transfers")
_BT_OFFER = re.compile(
    r"(?:special\s+offer|limited\s+time|you(?:'re| are)\s+(?:pre-)?approved|"
    r"balance\s+transfer\s+offer)"
    r".{0,120}?(?P<apr>[\d.]+)\s*%"
    r".{0,60}?(?P<months>\d{1,2})\s*(?:months?|mos\.?|billing\s+cycles?)",
    re.I | re.S,
)

_HEADER_NAMES = re.compile(
    r"^(plan|remaining|monthly\s*payment|payments?\s*left|interest\s*saving\s*balance)$",
    re.I,
)


def _money(raw: str | None) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        s = str(raw).replace(",", "").replace("$", "").strip()
        if not s:
            return None
        return Decimal(s).quantize(CENT)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _apr_rate(display: Decimal) -> Decimal:
    """22.99 or 0 → store-style rate (0 for zero, else fraction if > 1)."""
    if display == 0:
        return Decimal("0")
    if display > 1:
        return (display / Decimal("100")).quantize(Decimal("0.0001"))
    return display.quantize(Decimal("0.0001"))


def _extract_split_purchase_plans(text: str) -> list[dict[str, Any]]:
    """Issuer statement: N payments of $X, optional Payment K of N.

    BNPL brand lines (Klarna, Affirm, …) become future charges in bnpl.py,
    not a card carve-out — remaining installments have not posted yet.
    """
    from honestspend.services.bnpl import bnpl_brand

    out: list[dict[str, Any]] = []
    if not (
        _N_PAYMENTS_OF.search(text)
        or _PAYMENT_N_OF_M.search(text)
        or re.search(r"pay\s+in\s+4|payin4", text, re.I)
        or _ISB_HEADER.search(text)
    ):
        return out
    n_paid = None
    total = None
    pm = _PAYMENT_N_OF_M.search(text)
    if pm:
        try:
            n_paid = int(pm.group("n"))
            total = int(pm.group("m"))
        except (TypeError, ValueError):
            n_paid = total = None
    for m in _N_PAYMENTS_OF.finditer(text):
        window = text[max(0, m.start() - 80) : m.end() + 80]
        if bnpl_brand(window):
            continue
        monthly = _money(m.group("amt"))
        try:
            count = int(m.group("count"))
        except (TypeError, ValueError):
            continue
        if monthly is None or monthly <= 0 or count < 2 or count > 36:
            continue
        if total is None:
            total = count
        months_left = (total - n_paid + 1) if n_paid and total else count
        if months_left < 1:
            continue
        out.append(
            {
                "kind": "purchase_plan",
                "name": f"Pay in {total} · {monthly}",
                "principal_remaining": (monthly * months_left).quantize(CENT),
                "monthly_payment": monthly,
                "months_left": months_left,
                "source_kind": "split_purchase",
            }
        )
    if out:
        return out
    # Pay in 4 with a lone installment amount + N of M, no “N payments of”
    if total and n_paid and total >= 2 and not bnpl_brand(text):
        found = list(_MONEY_2.finditer(text))
        monthly = _money(found[-1].group(0)) if found else None
        if monthly and monthly > 0:
            months_left = total - n_paid + 1
            if months_left >= 1:
                out.append(
                    {
                        "kind": "purchase_plan",
                        "name": f"Pay in {total} · {monthly}",
                        "principal_remaining": (monthly * months_left).quantize(CENT),
                        "monthly_payment": monthly,
                        "months_left": months_left,
                        "source_kind": "split_purchase",
                    }
                )
    return out


def _extract_isb_plans(text: str) -> list[dict[str, Any]]:
    """Amazon-style Interest Saving Balance / plan table rows."""
    if not _ISB_HEADER.search(text) and not re.search(
        r"monthly\s+payment", text, re.I
    ):
        # Require table context — not a free-floating dollar amount
        return []

    out: list[dict[str, Any]] = []
    for m in _ISB_ROW.finditer(text):
        name = " ".join(m.group("name").split()).strip()
        if not name or _HEADER_NAMES.match(name):
            continue
        # Drop trailing column labels if stuck on name
        name = re.sub(
            r"\s+(Remaining|Monthly\s+payment|Payments?\s+left)\s*$",
            "",
            name,
            flags=re.I,
        ).strip()
        remaining = _money(m.group("remaining"))
        monthly = _money(m.group("monthly"))
        if remaining is None or monthly is None:
            continue
        try:
            months_left = int(m.group("left"))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "kind": "purchase_plan",
                "name": name,
                "principal_remaining": remaining,
                "monthly_payment": monthly,
                "months_left": months_left,
            }
        )
    return out


def _extract_card_intros(text: str) -> list[dict[str, Any]]:
    """Card-level intro / promotional APR windows with a promo balance."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for pat in (_INTRO, _INTRO_BAL_FIRST):
        for m in pat.finditer(text):
            bal = _money(m.group("bal"))
            end = _parse_date(m.group("end"))
            if bal is None or end is None:
                continue
            try:
                apr_disp = Decimal(str(m.group("apr")).replace(",", ""))
            except (InvalidOperation, ValueError):
                continue
            key = (end.isoformat(), str(bal))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "kind": "card_intro",
                    "name": "Intro 0%" if apr_disp == 0 else f"Intro {apr_disp}%",
                    "principal_remaining": bal,
                    "monthly_payment": Decimal("0.00"),
                    "end_date": end,
                    "apr": _apr_rate(apr_disp),
                }
            )
    return out


def extract_interest_saving_balance(text: str) -> Decimal | None:
    """Chase-style ISB: pay this amount this cycle to keep purchase grace."""
    if not text:
        return None
    m = _ISB_LUMP.search(str(text).replace("\xa0", " "))
    return _money(m.group("bal")) if m else None


def monthly_installment_plan_name(total_months: int) -> str:
    return f"Monthly installments · {int(total_months)} mo"


def is_monthly_installment_payee(payee: str | None) -> bool:
    """True for Apple-style MONTHLY INSTALLMENTS (11 OF 24)."""
    if not payee or not str(payee).strip():
        return False
    return bool(_INSTALLMENT_N_OF_M.search(str(payee).replace("\xa0", " ")))


def parse_monthly_installment(
    payee: str,
    amount: Decimal | None = None,
) -> dict[str, Any] | None:
    """One Apple-style N-of-M line → purchase_plan fields (uncombined)."""
    hit = _installment_hit(payee, amount)
    if hit is None:
        return None
    n, total, monthly = hit
    months_left = total - n + 1
    return {
        "kind": "purchase_plan",
        "name": monthly_installment_plan_name(total),
        "principal_remaining": (monthly * months_left).quantize(CENT),
        "monthly_payment": monthly,
        "months_left": months_left,
        "payment_number": n,
        "total_payments": total,
        "source_kind": "monthly_installment",
    }


def _installment_hit(
    payee: str,
    amount: Decimal | None = None,
) -> tuple[int, int, Decimal] | None:
    if not payee or not str(payee).strip():
        return None
    m = _INSTALLMENT_N_OF_M.search(str(payee).replace("\xa0", " "))
    if not m:
        return None
    try:
        n = int(m.group("n"))
        total = int(m.group("m"))
    except (TypeError, ValueError):
        return None
    if n < 1 or total < 2 or n > total:
        return None
    monthly: Decimal | None = None
    if amount is not None:
        monthly = abs(_money(str(amount)) or Decimal("0"))
        if monthly <= 0:
            monthly = None
    if monthly is None:
        found = list(_MONEY_2.finditer(str(payee).replace("\xa0", " ")))
        if found:
            monthly = _money(found[-1].group(0))
    if monthly is None or monthly <= 0:
        return None
    return n, total, monthly.quantize(CENT)


def group_installment_hits(
    hits: list[tuple[int, int, Decimal]],
) -> list[dict[str, Any]]:
    """Latest N-of-M per (term, monthly); same-amount rows that month are parallel plans."""
    latest: dict[tuple[int, Decimal], dict[str, Any]] = {}
    for n, total, monthly in hits:
        monthly_q = monthly.quantize(CENT)
        key = (int(total), monthly_q)
        cur = latest.get(key)
        if cur is None or n > cur["n"]:
            latest[key] = {"n": int(n), "total": int(total), "monthly": monthly_q, "count": 1}
        elif n == cur["n"]:
            cur["count"] += 1
    out: list[dict[str, Any]] = []
    for g in latest.values():
        monthly = (g["monthly"] * g["count"]).quantize(CENT)
        months_left = g["total"] - g["n"] + 1
        out.append(
            {
                "kind": "purchase_plan",
                "name": monthly_installment_plan_name(g["total"]),
                "principal_remaining": (monthly * months_left).quantize(CENT),
                "monthly_payment": monthly,
                "months_left": months_left,
                "payment_number": g["n"],
                "total_payments": g["total"],
                "source_kind": "monthly_installment",
            }
        )
    return out


def extract_installment_terms_from_payee_amounts(
    rows: list[tuple[str, Decimal | None]],
) -> list[dict[str, Any]]:
    """Structured activity rows (payee, amount) → grouped purchase_plan terms."""
    hits: list[tuple[int, int, Decimal]] = []
    for payee, amount in rows:
        hit = _installment_hit(payee or "", amount)
        if hit is not None:
            hits.append(hit)
    return group_installment_hits(hits)


def merge_installment_terms(
    *groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the fullest view of each plan. File text can see parallel same-amount
    rows that activity dedupe collapses to one transaction."""
    combined: list[dict[str, Any]] = []
    for g in groups:
        combined.extend(g)
    if not combined:
        return []
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for t in combined:
        key = (t.get("name"), t.get("total_payments"), t.get("months_left"))
        buckets.setdefault(key, []).append(t)
    out: list[dict[str, Any]] = []
    for group in buckets.values():
        group = sorted(group, key=lambda t: t["monthly_payment"], reverse=True)
        kept: list[dict[str, Any]] = []
        for t in group:
            m = t["monthly_payment"]
            if any(_monthly_is_multiple(big["monthly_payment"], m) for big in kept):
                continue
            kept.append(t)
        out.extend(kept)
    return out


def _monthly_is_multiple(big: Decimal, small: Decimal) -> bool:
    if small <= 0 or big < small:
        return False
    if big == small:
        return True
    n = (big / small).quantize(Decimal("1"))
    return n >= 2 and (small * n).quantize(CENT) == big.quantize(CENT)


def _extract_monthly_installments(text: str) -> list[dict[str, Any]]:
    """Apple-style N-of-M activity / statement lines → purchase_plan (ISB-class)."""
    hits: list[tuple[int, int, Decimal]] = []
    for line in str(text).replace("\xa0", " ").splitlines():
        line = " ".join(line.split())
        if not _INSTALLMENT_N_OF_M.search(line):
            continue
        hit = _installment_hit(line, None)
        if hit is not None:
            hits.append(hit)
    return group_installment_hits(hits)


def _extract_deferred_payoffs(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _DEFERRED_PAYOFF.finditer(text):
        bal = _money(m.group("bal"))
        end = _parse_date(m.group("end"))
        if bal is None or bal <= 0 or end is None:
            continue
        out.append(
            {
                "kind": "card_intro",
                "name": f"Deferred interest · due {end.isoformat()}",
                "principal_remaining": bal,
                "monthly_payment": Decimal("0.00"),
                "end_date": end,
                "apr": Decimal("0"),
            }
        )
    return out


def _extract_wf_promos(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _WF_PROMO.finditer(text):
        bal = _money(m.group("bal"))
        end = _parse_date(m.group("end"))
        if bal is None or bal <= 0 or end is None:
            continue
        try:
            apr_disp = Decimal(str(m.group("apr")))
        except (InvalidOperation, ValueError):
            apr_disp = Decimal("0")
        out.append(
            {
                "kind": "card_intro",
                "name": f"Promotional {apr_disp}% through {end.isoformat()}",
                "principal_remaining": bal,
                "monthly_payment": Decimal("0.00"),
                "end_date": end,
                "apr": _apr_rate(apr_disp),
            }
        )
    return out


def _extract_amex_intro(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _AMEX_INTRO.finditer(text):
        bal = _money(m.group("bal"))
        end = _parse_date(m.group("end"))
        if bal is None or bal <= 0 or end is None:
            continue
        try:
            apr_disp = Decimal(str(m.group("apr")))
        except (InvalidOperation, ValueError):
            apr_disp = Decimal("0")
        out.append(
            {
                "kind": "card_intro",
                "name": f"Introductory purchase {apr_disp}%",
                "principal_remaining": bal,
                "monthly_payment": Decimal("0.00"),
                "end_date": end,
                "apr": _apr_rate(apr_disp),
            }
        )
    return out


def _extract_equal_pay(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n = 0
    for m in _EQUAL_PAY.finditer(text):
        bal = _money(m.group("bal"))
        if bal is None or bal <= 0:
            continue
        n += 1
        end = _parse_date(m.group("end")) if m.group("end") else None
        try:
            apr_disp = Decimal(str(m.group("apr")))
        except (InvalidOperation, ValueError):
            apr_disp = Decimal("0")
        name = "Equal Pay Promo" if n == 1 else f"Equal Pay Promo {n}"
        out.append(
            {
                "kind": "purchase_plan",
                "name": name,
                "principal_remaining": bal,
                "monthly_payment": Decimal("0.00"),
                "end_date": end,
                "apr": _apr_rate(apr_disp),
            }
        )
    return out


def _extract_bt_offers(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # Skip boilerplate legal ("interest on balance transfers beginning")
    if not re.search(
        r"(?i)(balance\s+transfer\s+offer|special\s+offer|limited\s+time|"
        r"pre-?approved.{0,40}transfer)",
        text,
    ):
        return out
    for m in _BT_OFFER.finditer(text):
        try:
            apr_disp = Decimal(str(m.group("apr")))
            months = int(m.group("months"))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if months < 3 or months > 36:
            continue
        if apr_disp < 0 or apr_disp > 30:
            continue
        out.append(
            {
                "kind": "offer",
                "offer_type": "balance_transfer",
                "name": f"Balance transfer {apr_disp}% · {months} months",
                "principal_remaining": Decimal("0.00"),
                "monthly_payment": Decimal("0.00"),
                "apr": _apr_rate(apr_disp),
                "months_left": months,
            }
        )
    return out


def extract_promo_terms(text: str) -> list[dict[str, Any]]:
    """Each dict: kind, name, principal_remaining, monthly_payment, end_date?, months_left?,
    apr?, offer_type?. Never invent rows from a lone '0%' word without a balance or table."""
    if not text or not str(text).strip():
        return []

    t = str(text).replace("\xa0", " ")
    rows: list[dict[str, Any]] = []
    rows.extend(_extract_isb_plans(t))
    rows.extend(_extract_split_purchase_plans(t))
    rows.extend(_extract_monthly_installments(t))
    rows.extend(_extract_card_intros(t))
    rows.extend(_extract_deferred_payoffs(t))
    rows.extend(_extract_wf_promos(t))
    rows.extend(_extract_amex_intro(t))
    rows.extend(_extract_equal_pay(t))
    rows.extend(_extract_bt_offers(t))

    # Dedupe by (kind, end, remaining)
    seen: set[tuple[Any, ...]] = set()
    uniq: list[dict[str, Any]] = []
    for r in rows:
        key = (
            r.get("kind"),
            str(r.get("principal_remaining") or ""),
            r.get("end_date").isoformat() if r.get("end_date") else "",
            r.get("name"),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq
