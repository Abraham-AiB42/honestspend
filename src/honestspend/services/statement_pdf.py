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

from honestspend.db import Account, Transaction
from honestspend.services.categorizer import categorize_uncategorized
from honestspend.services.import_reminders import mark_import_activity

# Date patterns common on US statements
_DATE_RE = re.compile(
    r"\b((?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])(?:[/\-.](?:\d{2}|\d{4}))?)\b"
)
_AMOUNT_RE = re.compile(
    r"(?<![\w.])"  # not mid-word
    # optional surrounding parens for credit/overdraft style ($50.00)
    r"(\(?-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?|\(?-?\$?\s*\d+\.\d{2}\)?)"
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
    next_steps: list[dict[str, Any]] = field(default_factory=list)
    ending_balance: str | None = None
    institution_balance_set: bool = False
    books_balance: str | None = None
    promos_found: int = 0
    promo_conflicts: int = 0
    txn_mismatches: list[dict[str, Any]] = field(default_factory=list)
    drift: str | None = None
    balance_source: str | None = None  # new_balance | ending | none
    # High-confidence schedule advances after import (bill already on bank)
    schedules_advanced: int = 0
    schedules_advanced_names: list[str] = field(default_factory=list)
    schedule_advance_hint: str | None = None
    schedule_advance_error: str | None = None


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


def parse_vehicle_finance_statement(text: str) -> dict[str, Any] | None:
    """Hyundai / similar motor-finance monthly coupon (lease or loan)."""
    if not text:
        return None
    if not re.search(
        r"vehicle\s+description|motor\s+finance|lease\s+term|\bvin\s+number\b",
        text,
        re.I,
    ):
        return None
    vehicle = None
    vm = re.search(r"Vehicle Description\s+([^\n\r]+)", text, re.I)
    if vm:
        raw = " ".join(vm.group(1).split())
        # "2026 HYUNDAI PALISADE HYBRID" → Hyundai Palisade
        mm = re.search(
            r"(?:20\d{2}\s+)?(HYUNDAI|TOYOTA|HONDA|FORD|CHEVROLET|KIA|MAZDA|SUBARU|TESLA)\s+([A-Z][A-Z0-9\-]+)",
            raw,
            re.I,
        )
        if mm:
            vehicle = f"{mm.group(1).title()} {mm.group(2).title()}"
        else:
            vehicle = raw.title()[:40]
    lender = None
    if re.search(r"hyundai\s+motor\s+finance", text, re.I):
        lender = "Hyundai Motor Finance"
    elif re.search(r"toyota\s+financial", text, re.I):
        lender = "Toyota Financial"
    acct = None
    am = re.search(r"Account Number\s+(\d{6,})", text, re.I)
    if am:
        acct = am.group(1)
    due = None
    dm = re.search(
        r"(?:Your\s+)?Total Amount Due\s*\$?\s*([\d,]+\.\d{2})",
        text,
        re.I,
    )
    if dm:
        due = _parse_amount(dm.group(1))
    return {
        "kind": "loan",
        "loan_label": "Car loan",
        "loan_detail": vehicle,
        "lender": lender,
        "acctid": acct,
        "last4": acct[-4:] if acct and len(acct) >= 4 else None,
        "ledger_balance": str(due) if due is not None else None,
        "product": vehicle or lender,
    }


def parse_credit_union_membership(text: str) -> list[dict[str, Any]]:
    """Canvas-style membership statement: ID: 00/01/02 shares under one member number."""
    if not text:
        return []
    if not re.search(r"\bID:\s*0*\d{1,2}\b", text, re.I):
        return []
    if not re.search(r"credit union|canvas\.org|share\s+0?\d", text, re.I):
        return []
    org = "Credit union"
    if re.search(r"canvas\.org|canvas credit union", text, re.I):
        org = "Canvas Credit Union"
    member = None
    mm = re.search(r"Account Number\s+X+(\d{4})", text, re.I)
    if mm:
        member = mm.group(1)
    shares: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"ID:\s*0*(?P<id>\d{1,2})\s+(?P<label>SAVINGS ACCOUNT|FREE CHECKING|"
        r"HIGH YIELD MONEY MARKET|MONEY MARKET|CHECKING|SAVINGS|[A-Z][A-Z /-]{3,32})",
        text,
        re.I,
    ):
        sid = f"{int(m.group('id')):02d}"
        if sid in seen:
            continue
        seen.add(sid)
        label = " ".join(m.group("label").split()).strip(" -")
        low = label.lower()
        if "check" in low:
            kind = "checking"
            friendly = "Checking"
        elif "money market" in low or "mma" in low:
            kind = "savings"
            friendly = "Money market"
        elif "sav" in low:
            kind = "savings"
            friendly = "Savings"
        else:
            kind = "checking"
            friendly = label.title()[:32]
        ending = None
        # Prefer "Ending Balance for LABEL 1,234.56" after this share
        tail = text[m.end() : m.end() + 1800]
        em = re.search(
            rf"Ending Balance for\s+{re.escape(label)}\s+\$?\s*([\d,]+\.\d{{2}})",
            tail,
            re.I,
        )
        if not em:
            em = re.search(
                r"Ending Balance for[^\n]{0,40}\s+\$?\s*([\d,]+\.\d{2})",
                tail,
                re.I,
            )
        if em:
            ending = _parse_amount(em.group(1))
        share_num = f"{int(sid):04d}"
        shares.append(
            {
                "share_id": sid,
                "label": label,
                "kind": kind,
                "friendly": friendly,
                "org": org,
                "member_last4": member,
                "acctid": share_num,
                "last4": share_num,
                "match_tails": [share_num, sid, str(int(sid))],
                "ledger_balance": str(ending) if ending is not None else None,
                "apply_balance_only": True,
                "is_statement": True,
            }
        )
    return shares


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


_PROMO_DISCLOSURE = re.compile(
    r"promotional\s+rate\s+expires|"
    r"rate\s+expires\s+\d|"
    r"deferred\s+interest|"
    r"promotional\s+balance\s+of|"
    r"equal\s+pay\s+promo|"
    r"introductory\s+purchase|"
    r"interest\s+saving\s+balance|"
    r"to avoid paying deferred|"
    r"balance\s+subject\s+to\s+promo|"
    r"\b\d+\.\d{1,2}\s*%\s+\$",
    re.I,
)
_PROMO_PAYEE = re.compile(
    r"^\s*\d+\.\d{1,2}\s*%|"
    r"deferred interest|"
    r"promotional rate|"
    r"equal pay promo|"
    r"rate expires|"
    r"promotional balance",
    re.I,
)


def is_promo_disclosure_line(text: str, *, payee: str | None = None) -> bool:
    """True for promo APR / deferred-interest table rows — not spend."""
    blob = f"{text or ''} {payee or ''}"
    if _PROMO_DISCLOSURE.search(blob):
        return True
    if _PROMO_PAYEE.search(payee or "") or _PROMO_PAYEE.search(text or ""):
        return True
    return False


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
        if is_promo_disclosure_line(line):
            continue
        # Skip summary headers only when there is no date (keep dated INTEREST CHARGED / fees)
        low = line.lower()
        dm = _DATE_RE.search(line)
        if dm is None and any(
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
        # skip pure totals (keep bare "PAYMENT" — real pay-down rows on many statements)
        if mid.lower() in ("total", "subtotal", "balance"):
            continue
        if is_promo_disclosure_line(line, payee=mid):
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


# Statement summary lines (not txn rows) — prefer most authoritative label
_ENDING_BAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bnew\s+balance\b"), "new_balance"),
    (re.compile(r"(?i)\btotal\s+balance\b"), "total_balance"),
    (re.compile(r"(?i)\bstandard\s+balance\b"), "standard_balance"),
    (re.compile(r"(?i)\bclosing\s+balance\b"), "closing"),
    (re.compile(r"(?i)\bending\s+balance\b"), "ending"),
    (re.compile(r"(?i)\bstatement\s+balance\b"), "statement"),
    (re.compile(r"(?i)\bcurrent\s+balance\b"), "current"),
]

_LABELED_NEW_BAL = re.compile(
    r"(?i)(?:new\s+balance|total\s+balance)\s*[:.]?\s*\$?\s*([\d,]+\.\d{2})"
)


def parse_statement_ending_balance(text: str) -> tuple[Decimal | None, str | None]:
    """Extract statement ending / new balance from summary lines (not txn rows).

    Returns (balance, source_label) or (None, None). Prefers New Balance over
    Ending/Closing when multiple matches exist.
    """
    best: Decimal | None = None
    best_src: str | None = None
    best_rank = 999
    # Prefer an explicit "New Balance $1,234.56" even on a jammed header line
    labeled = _LABELED_NEW_BAL.findall(text or "")
    if labeled:
        amt = _parse_amount(labeled[0])
        if amt is not None:
            return amt, "new_balance"

    for line in text.splitlines():
        line_c = " ".join(line.split())
        if len(line_c) < 8:
            continue
        low = line_c.lower()
        # Skip previous / min / pay-off-warning lines (not ending bal)
        if any(
            x in low
            for x in (
                "previous balance",
                "past due",
                "minimum payment",
                "pay off the new balance",
                "will pay off",
                "credit limit",
                "available credit",
            )
        ):
            continue
        # "Payment Due Date … New Balance $199.90" is OK — labeled extractor already ran
        if "payment due" in low and "new balance" not in low and "total balance" not in low:
            continue
        rank = None
        src = None
        for i, (pat, label) in enumerate(_ENDING_BAL_PATTERNS):
            if pat.search(line_c):
                rank = i
                src = label
                break
        if rank is None:
            continue
        amounts = list(_AMOUNT_RE.finditer(line_c))
        if not amounts:
            continue
        amt = _parse_amount(amounts[-1].group(1))
        if amt is None:
            continue
        if rank < best_rank:
            best_rank = rank
            # Keep sign: overdraft / credit overpayment must not become positive bank truth
            best = amt
            best_src = src
    return best, best_src


def preview_statement_pdf(file_obj: BinaryIO | bytes | Path | str) -> dict[str, Any]:
    text, pages = extract_pdf_text(file_obj)
    rows = parse_statement_lines(text)
    ending, bal_src = parse_statement_ending_balance(text)
    return {
        "ok": bool(rows) or pages > 0 or ending is not None,
        "pages": pages,
        "raw_text_chars": len(text),
        "candidates": len(rows),
        "ending_balance": str(ending) if ending is not None else None,
        "balance_source": bal_src,
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
            if not rows and ending is None
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

    from honestspend.services.import_dedupe import load_dedupe_index
    from honestspend.services.import_txn_review import (
        find_existing_by_external_id,
        txn_mismatch_reviews,
    )

    dedupe = load_dedupe_index(session, account_id)

    for r in rows:
        amt = r["amount"]
        payee = r["payee"]
        if amount_sign == "invert":
            amt = -amt
        if acct.kind == "credit" and amount_sign in ("bank", "invert"):
            # Charges → negative; payments/refunds → positive
            from honestspend.services.import_amounts import normalize_credit_import_amount

            amt = normalize_credit_import_amount(amt, payee)
        d = r["txn_date"]
        ext = dedupe.preferred_external_id(
            txn_date=d, amount=amt, payee=payee, source="pdf"
        )
        # D12: external_id match — never overwrite books amount/date/payee/category
        existing = find_existing_by_external_id(session, account_id, ext)
        if existing is not None:
            mm = txn_mismatch_reviews(
                existing=existing,
                incoming={"amount": amt, "txn_date": d, "payee": payee},
            )
            if mm:
                result.txn_mismatches.append(mm)
            result.skipped_existing += 1
            continue
        if dedupe.is_duplicate(txn_date=d, amount=amt, payee=payee):
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
            from honestspend.services.account_balance import apply_amount_to_account

            apply_amount_to_account(acct, amt)
            dedupe.remember(
                txn_date=d, amount=amt, payee=payee, external_id=ext
            )
            result.transactions_created += 1
        except Exception as e:
            result.skipped_bad += 1
            if len(result.errors) < 5:
                result.errors.append(str(e))

    session.flush()

    # APR / limit / promo / min payment from statement text
    try:
        from honestspend.services.import_bootstrap import enrich_account_from_text

        enrich_account_from_text(session, account_id, text, only_if_empty=True)
    except Exception:
        pass

    # Apply ISB / intro promo terms from statement text (after enrich)
    try:
        from honestspend.services.import_bootstrap import apply_statement_promos

        promo_out = apply_statement_promos(session, account_id, text)
        result.promos_found = int(promo_out.get("promos_found") or 0)
        result.promo_conflicts = int(promo_out.get("promo_conflicts") or 0)
    except Exception:
        pass

    # New Balance / Ending Balance → institution_balance (CSV/OFX parity)
    drift: Decimal | None = None
    ending, bal_src = parse_statement_ending_balance(text)
    if ending is None:
        try:
            from honestspend.services.import_bootstrap import extract_statement_enrichment

            en0 = extract_statement_enrichment(text)
            if en0.get("new_balance") is not None:
                ending = en0["new_balance"]
                bal_src = "new_balance"
        except Exception:
            pass
    if ending is not None:
        from honestspend.services.reconcile import set_institution_balance

        set_institution_balance(session, account_id, ending, mark_reconciled=False)
        result.institution_balance_set = True
        result.ending_balance = str(ending)
        result.balance_source = bal_src
        session.refresh(acct)
        books = Decimal(str(acct.current_balance or 0))
        result.books_balance = str(books)
        drift = (books - ending).quantize(Decimal("0.01"))
        result.drift = str(drift)
        try:
            from honestspend.services.import_bootstrap import extract_statement_enrichment
            from honestspend.services.statement_freeze import freeze_statement_cycle

            en = extract_statement_enrichment(text)
            close = en.get("statement_close_date")
            due = en.get("payment_due_date")
            start = en.get("statement_period_start")
            if acct.kind == "credit" and (close or due or acct.statement_close_day):
                freeze_statement_cycle(
                    session,
                    account_id,
                    actual_balance=ending,
                    cycle_end=close,
                    due_date=due,
                    cycle_start=start,
                    source="import",
                )
        except Exception:
            pass
    else:
        result.books_balance = str(acct.current_balance or 0)
        result.balance_source = "none"

    if result.transactions_created or result.institution_balance_set:
        try:
            mark_import_activity(session)
        except Exception:
            pass
        if auto_categorize and result.transactions_created:
            applied = categorize_uncategorized(
                session,
                profile_id=acct.profile_id,
                limit=result.transactions_created + 50,
                apply=True,
                use_grok=False,
                min_confidence=0.85,
            )
            result.categorized = sum(1 for x in applied if x.get("applied"))

    # Honesty: bank already paid the bill — advance matching schedules (high confidence only)
    if result.transactions_created > 0:
        try:
            from honestspend.services.schedule_mark_paid import advance_schedules_after_import

            adv = advance_schedules_after_import(
                session,
                account_id=account_id,
                profile_id=acct.profile_id,
                auto_apply=True,
            )
            result.schedules_advanced = int(adv.get("advanced_count") or 0)
            result.schedules_advanced_names = [
                str(x.get("name") or "") for x in (adv.get("advanced") or []) if x.get("name")
            ]
            result.schedule_advance_hint = adv.get("hint")
        except Exception as e:
            # Import still succeeded; surface advance failure without failing the import
            result.schedules_advanced = 0
            result.schedule_advance_error = str(e)[:300]

    from honestspend.services.import_brief import build_post_import_next_steps

    result.next_steps = build_post_import_next_steps(
        session,
        profile_id=acct.profile_id,
        account_id=account_id,
        created=result.transactions_created,
        categorized=result.categorized,
        skipped_existing=result.skipped_existing,
        drift=drift,
        books_balance=result.books_balance,
        institution_balance=result.ending_balance if result.institution_balance_set else None,
        source="PDF",
        promos_found=result.promos_found,
        promo_conflicts=result.promo_conflicts,
        txn_mismatches=result.txn_mismatches,
    )
    return result
