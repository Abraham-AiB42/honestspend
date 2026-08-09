"""Best-effort PDF bank statement line extraction (freeware).

Text PDFs only — scanned/image PDFs need OCR later. Heuristic parsers; always
review after import. Prefer CSV when the bank offers it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction
from financial_os.services.categorizer import categorize_uncategorized
from financial_os.services.import_reminders import mark_import_activity

# Date patterns common on US statements
_DATE_RE = re.compile(
    r"\b((?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])(?:[/\-.](?:\d{2}|\d{4}))?)\b"
)
_AMOUNT_RE = re.compile(
    r"(?<![\w.])"  # not mid-word
    r"(-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})|-?\$?\s*\d+\.\d{2})"
    r"(?!\d)"
)
_YEAR_HINT = re.compile(r"\b(20\d{2})\b")


@dataclass
class PdfImportResult:
    pages: int = 0
    lines_scanned: int = 0
    transactions_created: int = 0
    skipped_existing: int = 0
    skipped_bad: int = 0
    errors: list[str] = field(default_factory=list)
    categorized: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)
    raw_text_chars: int = 0


def extract_pdf_text(file_obj: BinaryIO | bytes | Path | str) -> tuple[str, int]:
    """Return full text + page count. Raises if pypdf missing or unreadable."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError(
            "PDF import needs pypdf. Install: pip install pypdf"
        ) from e

    if isinstance(file_obj, (str, Path)):
        reader = PdfReader(str(file_obj))
    elif isinstance(file_obj, bytes):
        reader = PdfReader(BytesIO(file_obj))
    else:
        raw = file_obj.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        reader = PdfReader(BytesIO(raw))

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts), len(reader.pages)


def _parse_amount(raw: str) -> Decimal | None:
    s = raw.replace("$", "").replace(",", "").replace(" ", "").strip()
    # parentheses for credit-card credits on some statements
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(raw: str, default_year: int) -> date | None:
    raw = raw.strip().replace(".", "/")
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y", "%m/%d", "%m-%d"):
        try:
            d = datetime.strptime(raw, fmt)
            if d.year == 1900:
                d = d.replace(year=default_year)
            # 2-digit year handled by %y
            if d.year < 100:
                d = d.replace(year=2000 + d.year)
            return d.date()
        except ValueError:
            continue
    return None


def parse_statement_lines(
    text: str,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Heuristic: lines with a date and a trailing amount become candidates."""
    as_of = as_of or date.today()
    years = [int(y) for y in _YEAR_HINT.findall(text)]
    default_year = max(years) if years else as_of.year

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = " ".join(line.split())
        if len(line) < 8:
            continue
        # skip obvious headers
        low = line.lower()
        if any(
            h in low
            for h in (
                "previous balance",
                "new balance",
                "minimum payment",
                "payment due",
                "account number",
                "page ",
                "total fees",
                "interest charged",
                "credit limit",
            )
        ):
            continue

        dm = _DATE_RE.search(line)
        if not dm:
            continue
        amounts = list(_AMOUNT_RE.finditer(line))
        if not amounts:
            continue
        # prefer last amount on the line (usual statement layout)
        am = amounts[-1]
        amt = _parse_amount(am.group(1))
        if amt is None or amt == 0:
            continue
        txn_date = _parse_date(dm.group(1), default_year)
        if txn_date is None:
            continue
        # payee: text between date and amount
        mid = line[dm.end() : am.start()].strip(" -·|\t")
        if not mid or len(mid) < 2:
            mid = line[dm.end() :].strip()
            mid = _AMOUNT_RE.sub("", mid).strip(" -·|\t")
        if len(mid) < 2:
            continue
        # skip pure totals
        if mid.lower() in ("total", "subtotal", "balance", "payment"):
            continue

        key = f"{txn_date.isoformat()}|{mid[:40].lower()}|{amt}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "txn_date": txn_date,
                "payee": mid[:200],
                "amount": amt,
                "line": line[:240],
            }
        )
    return rows


def preview_statement_pdf(file_obj: BinaryIO | bytes | Path | str) -> dict[str, Any]:
    text, pages = extract_pdf_text(file_obj)
    rows = parse_statement_lines(text)
    return {
        "ok": bool(rows) or pages > 0,
        "pages": pages,
        "raw_text_chars": len(text),
        "candidates": len(rows),
        "sample": [
            {
                "txn_date": r["txn_date"].isoformat(),
                "payee": r["payee"],
                "amount": str(r["amount"]),
            }
            for r in rows[:12]
        ],
        "hint": (
            "Text PDF only — if sample is empty, export CSV from the bank instead."
            if not rows
            else "Review sample before import; amounts may need Invert signs."
        ),
    }


def import_statement_pdf(
    session: Session,
    *,
    account_id: int,
    file_obj: BinaryIO | bytes | Path | str,
    filename: str = "statement.pdf",
    auto_categorize: bool = True,
    amount_sign: str = "bank",
) -> PdfImportResult:
    """Import heuristically extracted rows as transactions (external_id pdf:hash)."""
    result = PdfImportResult()
    acct = session.get(Account, account_id)
    if not acct:
        result.errors.append("Account not found")
        return result

    try:
        text, pages = extract_pdf_text(file_obj)
    except Exception as e:
        result.errors.append(str(e))
        return result

    result.pages = pages
    result.raw_text_chars = len(text)
    if not text.strip():
        result.errors.append(
            "No extractable text (scanned PDF?). Use CSV export or OCR later."
        )
        return result

    rows = parse_statement_lines(text)
    result.lines_scanned = len(rows)
    result.sample = [
        {
            "txn_date": r["txn_date"].isoformat(),
            "payee": r["payee"],
            "amount": str(r["amount"]),
        }
        for r in rows[:8]
    ]

    existing = {
        e
        for (e,) in session.query(Transaction.external_id)
        .filter(
            Transaction.account_id == account_id,
            Transaction.external_id.isnot(None),
        )
        .all()
        if e
    }

    for r in rows:
        amt = r["amount"]
        if amount_sign == "invert":
            amt = -amt
        # bank style: expenses negative — if statement shows charges as positive, flip for credit cards
        if acct.kind == "credit" and amt > 0:
            # charges increase balance owed → store as negative spend convention
            amt = -amt
        payee = r["payee"]
        d = r["txn_date"]
        ext = f"pdf:{account_id}:{d.isoformat()}:{payee[:48]}:{amt}"
        ext = ext[:200]
        if ext in existing:
            result.skipped_existing += 1
            continue
        try:
            session.add(
                Transaction(
                    profile_id=acct.profile_id,
                    account_id=acct.id,
                    txn_date=d,
                    amount=amt,
                    payee=payee,
                    status="cleared",
                    external_id=ext,
                    memo=f"PDF import · {filename}"[:200],
                )
            )
            existing.add(ext)
            result.transactions_created += 1
        except Exception as e:
            result.skipped_bad += 1
            if len(result.errors) < 5:
                result.errors.append(str(e))

    session.flush()
    if result.transactions_created:
        try:
            mark_import_activity(session)
        except Exception:
            pass
        if auto_categorize:
            applied = categorize_uncategorized(
                session,
                profile_id=acct.profile_id,
                limit=result.transactions_created + 50,
                apply=True,
                use_grok=False,
                min_confidence=0.85,
            )
            result.categorized = sum(1 for x in applied if x.get("applied"))

    return result
