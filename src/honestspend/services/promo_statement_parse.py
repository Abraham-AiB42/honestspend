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
_ISB_HEADER = re.compile(
    r"Interest\s+Saving\s+Balance|"
    r"^\s*Plan\s+Remaining\s+Monthly\s+payment\s+Payments?\s+left\s*$",
    re.I | re.M,
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
