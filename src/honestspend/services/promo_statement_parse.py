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


def extract_promo_terms(text: str) -> list[dict[str, Any]]:
    """Each dict: kind, name, principal_remaining, monthly_payment, end_date?, months_left?,
    apr?, offer_type?. Never invent rows from a lone '0%' word without a balance or table."""
    if not text or not str(text).strip():
        return []

    t = str(text).replace("\xa0", " ")
    rows: list[dict[str, Any]] = []
    rows.extend(_extract_isb_plans(t))
    rows.extend(_extract_card_intros(t))
    return rows
