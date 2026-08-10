"""Import transactions from bank/credit CSV downloads."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction
from financial_os.services.categorizer import categorize_uncategorized

DATE_KEYS = (
    "date",
    "transaction date",
    "posted date",
    "posting date",
    "trans date",
    "txn date",
    "post date",
)
DESC_KEYS = (
    "description",
    "payee",
    "name",
    "memo",
    "narrative",
    "transaction description",
    "details",
    "merchant",
)
AMOUNT_KEYS = ("amount", "transaction amount", "amt", "value")
DEBIT_KEYS = ("debit", "withdrawal", "outflow", "spend")
CREDIT_KEYS = ("credit", "deposit", "inflow")
# Running / ending balance columns (common on bank CSV exports)
BALANCE_KEYS = (
    "balance",
    "running balance",
    "running bal",
    "account balance",
    "available balance",
    "ending balance",
    "ledger balance",
    "current balance",
    "bal",
)
# Bank-supplied unique ids (stronger dedupe than date+amount+payee)
TXN_ID_KEYS = (
    "transaction id",
    "trans id",
    "txn id",
    "fit id",
    "fitid",
    "reference",
    "reference number",
    "ref number",
    "ref #",
    "check number",
    "check #",
    "serial",
    "bank reference",
    "confirmation",
    "confirmation number",
)


@dataclass
class CsvImportResult:
    rows_scanned: int = 0
    transactions_created: int = 0
    skipped_existing: int = 0
    skipped_bad: int = 0
    errors: list[str] = field(default_factory=list)
    categorized: int = 0
    next_steps: list[dict[str, Any]] = field(default_factory=list)
    ending_balance: str | None = None
    institution_balance_set: bool = False
    books_balance: str | None = None
    drift: str | None = None
    balance_source: str | None = None  # column | override | none


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _csv_looks_newest_first(pairs: list[tuple[date, Decimal]]) -> bool:
    """Heuristic: majority of consecutive date steps decrease → newest-first export."""
    if len(pairs) < 2:
        return False
    if pairs[0][0] > pairs[-1][0]:
        return True
    if pairs[0][0] < pairs[-1][0]:
        return False
    down = up = 0
    for i in range(1, len(pairs)):
        if pairs[i][0] < pairs[i - 1][0]:
            down += 1
        elif pairs[i][0] > pairs[i - 1][0]:
            up += 1
    if down > up:
        return True
    if up > down:
        return False
    # All same date without amount signals: retail default newest-first
    unique_dates = {d for d, _ in pairs}
    return len(unique_dates) == 1 and len(pairs) > 1


def _same_day_orientation_from_amounts(
    rows: list[tuple[Decimal, Decimal]],
) -> str | None:
    """Return 'newest_first' | 'chrono' | None from (running_bal, amount) same-day rows.

    Chronological: bal[i] ≈ bal[i-1] + amount[i]
    Newest-first:  bal[i] ≈ bal[i+1] + amount[i]  (amount on row i took older→newer)
    """
    if len(rows) < 2:
        return None
    tol = Decimal("0.02")
    chrono_ok = True
    for i in range(1, len(rows)):
        prev_bal, _ = rows[i - 1]
        bal, amt = rows[i]
        if abs((prev_bal + amt) - bal) > tol:
            chrono_ok = False
            break
    reverse_ok = True
    for i in range(len(rows) - 1):
        bal, amt = rows[i]
        older_bal, _ = rows[i + 1]
        if abs((older_bal + amt) - bal) > tol:
            reverse_ok = False
            break
    if reverse_ok and not chrono_ok:
        return "newest_first"
    if chrono_ok and not reverse_ok:
        return "chrono"
    return None


def ending_balance_from_pairs(
    pairs: list[tuple[date, Decimal]],
    amounts: list[Decimal | None] | None = None,
) -> Decimal | None:
    """Pick institution ending bal from (txn_date, balance) samples.

    Uses balance on max(txn_date). Orientation:
      · multi-day date order heuristic
      · same-day with amounts: reconstruct chrono vs newest-first from deltas
      · same-day without amounts: retail newest-first default
    """
    if not pairs:
        return None
    if amounts is not None and len(amounts) != len(pairs):
        amounts = None
    max_d = max(d for d, _ in pairs)
    same_idx = [i for i, (d, _) in enumerate(pairs) if d == max_d]
    same_bals = [pairs[i][1] for i in same_idx]
    if not same_bals:
        return None
    if len(same_bals) == 1:
        return same_bals[0]

    # Max-day multi-row: prefer amount-consistency on that day's slice (multi-day OK)
    if amounts is not None and len(same_idx) >= 2:
        amts = [amounts[i] for i in same_idx]
        if all(a is not None for a in amts):
            orient = _same_day_orientation_from_amounts(
                [(pairs[i][1], amounts[i]) for i in same_idx]  # type: ignore[misc]
            )
            if orient == "newest_first":
                return same_bals[0]
            if orient == "chrono":
                return same_bals[-1]

    if _csv_looks_newest_first(pairs):
        return same_bals[0]
    return same_bals[-1]


def _find_col(headers: list[str], keys: tuple[str, ...]) -> int | None:
    norms = [_norm(h) for h in headers]
    for k in keys:
        if k in norms:
            return norms.index(k)
    for i, h in enumerate(norms):
        for k in keys:
            if k in h:
                return i
    return None


def _parse_date(val: str) -> date | None:
    val = (val or "").strip().strip('"')
    if not val:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%m-%d-%Y",
    ):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    # ISO-ish
    try:
        return datetime.fromisoformat(val[:10]).date()
    except ValueError:
        return None


def _parse_amount(val: str) -> Decimal | None:
    if val is None:
        return None
    s = str(val).strip().strip('"')
    if not s:
        return None
    # (123.45) → -123.45
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.endswith("-"):
        neg = True
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if neg:
        d = -abs(d)
    return d


def _detect_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample[:4096], delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def preview_bank_csv(
    file_obj: TextIO | BinaryIO | str | Path,
    *,
    max_rows: int = 8,
) -> dict[str, Any]:
    """Detect columns and sample rows without writing to DB."""
    if isinstance(file_obj, (str, Path)):
        text = Path(file_obj).read_text(encoding="utf-8-sig", errors="replace")
    else:
        raw = file_obj.read()
        if isinstance(raw, bytes):
            text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = raw

    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines[:15]):
        if "," in line or "\t" in line or ";" in line:
            low = line.lower()
            if any(k in low for k in ("date", "amount", "description", "debit", "credit", "payee")):
                start = i
                break
    body = "\n".join(lines[start:])
    dialect = _detect_dialect(body)
    reader = csv.reader(io.StringIO(body), dialect)
    try:
        headers = next(reader)
    except StopIteration:
        return {"ok": False, "errors": ["Empty CSV"], "headers": [], "mapping": {}, "sample": []}

    date_i = _find_col(headers, DATE_KEYS)
    desc_i = _find_col(headers, DESC_KEYS)
    amt_i = _find_col(headers, AMOUNT_KEYS)
    debit_i = _find_col(headers, DEBIT_KEYS)
    credit_i = _find_col(headers, CREDIT_KEYS)
    bal_i = _find_col(headers, BALANCE_KEYS)
    id_i = _find_col(headers, TXN_ID_KEYS)
    # Avoid treating a lone "id" column as date-adjacent noise when it's the only id-like header
    if id_i is None:
        for i, h in enumerate(headers):
            if _norm(h) in ("id", "transactionid", "transid"):
                id_i = i
                break

    mapping = {
        "date_col": headers[date_i] if date_i is not None else None,
        "date_index": date_i,
        "description_col": headers[desc_i] if desc_i is not None else None,
        "description_index": desc_i,
        "amount_col": headers[amt_i] if amt_i is not None else None,
        "amount_index": amt_i,
        "debit_col": headers[debit_i] if debit_i is not None else None,
        "debit_index": debit_i,
        "credit_col": headers[credit_i] if credit_i is not None else None,
        "credit_index": credit_i,
        "balance_col": headers[bal_i] if bal_i is not None else None,
        "balance_index": bal_i,
        "txn_id_col": headers[id_i] if id_i is not None else None,
        "txn_id_index": id_i,
    }
    errors: list[str] = []
    if date_i is None:
        errors.append("Could not find date column")
    if amt_i is None and debit_i is None and credit_i is None:
        errors.append("Could not find amount/debit/credit columns")

    sample: list[dict[str, Any]] = []
    scanned = 0
    bal_pairs: list[tuple[date, Decimal]] = []
    bal_amounts: list[Decimal | None] = []
    for row in reader:
        if not row or all(not (c or "").strip() for c in row):
            continue
        scanned += 1
        d = _parse_date(row[date_i] if date_i is not None and date_i < len(row) else "")
        payee = (row[desc_i] if desc_i is not None and desc_i < len(row) else "") or ""
        amount: Decimal | None = None
        if amt_i is not None and amt_i < len(row):
            amount = _parse_amount(row[amt_i])
        else:
            deb = _parse_amount(row[debit_i]) if debit_i is not None and debit_i < len(row) else None
            cred = _parse_amount(row[credit_i]) if credit_i is not None and credit_i < len(row) else None
            if deb and deb != 0:
                amount = -abs(deb)
            elif cred and cred != 0:
                amount = abs(cred)
        bal: Decimal | None = None
        if bal_i is not None and bal_i < len(row):
            bal = _parse_amount(row[bal_i])
            if bal is not None and d is not None:
                bal_pairs.append((d, bal))
                bal_amounts.append(amount)
        if len(sample) < max_rows:
            sample.append(
                {
                    "date": d.isoformat() if d else None,
                    "payee": payee.strip()[:80],
                    "amount": str(amount) if amount is not None else None,
                    "balance": str(bal) if bal is not None else None,
                    "raw": [c[:40] for c in row[:8]],
                }
            )

    # Continue scan for balance pairs beyond preview sample
    if bal_i is not None:
        for row in reader:
            if not row or bal_i >= len(row):
                continue
            d = _parse_date(row[date_i] if date_i is not None and date_i < len(row) else "")
            amount: Decimal | None = None
            if amt_i is not None and amt_i < len(row):
                amount = _parse_amount(row[amt_i])
            else:
                deb = (
                    _parse_amount(row[debit_i])
                    if debit_i is not None and debit_i < len(row)
                    else None
                )
                cred = (
                    _parse_amount(row[credit_i])
                    if credit_i is not None and credit_i < len(row)
                    else None
                )
                if deb and deb != 0:
                    amount = -abs(deb)
                elif cred and cred != 0:
                    amount = abs(cred)
            bal = _parse_amount(row[bal_i])
            if bal is not None and d is not None:
                bal_pairs.append((d, bal))
                bal_amounts.append(amount)

    ending = ending_balance_from_pairs(bal_pairs, bal_amounts)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "headers": headers,
        "mapping": mapping,
        "sample": sample,
        "rows_previewed": scanned,
        "ending_balance": str(ending) if ending is not None else None,
        "hint": (
            "Confirm mapping looks right, then import. "
            + (
                "Balance column found — ending bal is the balance on the latest txn date. "
                if ending is not None
                else "No balance column — you can enter bank ending balance on Import. "
            )
            + "Use amount_sign=invert if signs are flipped."
        ),
    }


def import_bank_csv(
    session: Session,
    *,
    account_id: int,
    file_obj: TextIO | BinaryIO | str | Path,
    filename: str = "import.csv",
    auto_categorize: bool = True,
    amount_sign: str = "bank",  # bank: expenses negative; invert: flip signs
    institution_balance: Decimal | None = None,
    apply_ending_balance: bool = True,
) -> CsvImportResult:
    result = CsvImportResult()
    acct = session.get(Account, account_id)
    if not acct:
        result.errors.append("Account not found")
        return result

    if isinstance(file_obj, (str, Path)):
        text = Path(file_obj).read_text(encoding="utf-8-sig", errors="replace")
    else:
        raw = file_obj.read()
        if isinstance(raw, bytes):
            text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = raw

    # Strip BOM / blank lines at top (some banks put titles)
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines[:15]):
        if "," in line or "\t" in line or ";" in line:
            # header-like if has a date-ish word or amount
            low = line.lower()
            if any(k in low for k in ("date", "amount", "description", "debit", "credit", "payee", "balance")):
                start = i
                break
    body = "\n".join(lines[start:])
    dialect = _detect_dialect(body)
    reader = csv.reader(io.StringIO(body), dialect)
    try:
        headers = next(reader)
    except StopIteration:
        result.errors.append("Empty CSV")
        return result

    date_i = _find_col(headers, DATE_KEYS)
    desc_i = _find_col(headers, DESC_KEYS)
    amt_i = _find_col(headers, AMOUNT_KEYS)
    debit_i = _find_col(headers, DEBIT_KEYS)
    credit_i = _find_col(headers, CREDIT_KEYS)
    bal_i = _find_col(headers, BALANCE_KEYS)
    id_i = _find_col(headers, TXN_ID_KEYS)
    if id_i is None:
        for i, h in enumerate(headers):
            if _norm(h) in ("id", "transactionid", "transid"):
                id_i = i
                break

    if date_i is None:
        result.errors.append(f"Could not find date column in {headers}")
        return result
    if amt_i is None and debit_i is None and credit_i is None:
        result.errors.append(f"Could not find amount/debit/credit columns in {headers}")
        return result
    if desc_i is None:
        # use first non-date string column
        for i, h in enumerate(headers):
            if i != date_i and i != amt_i:
                desc_i = i
                break

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

    bal_pairs: list[tuple[date, Decimal]] = []
    bal_amounts: list[Decimal | None] = []
    for row in reader:
        result.rows_scanned += 1
        if not row or all(not (c or "").strip() for c in row):
            continue
        try:
            d = _parse_date(row[date_i] if date_i < len(row) else "")
            if not d:
                result.skipped_bad += 1
                continue
            payee = ""
            if desc_i is not None and desc_i < len(row):
                payee = (row[desc_i] or "").strip()

            amount: Decimal | None = None
            if amt_i is not None and amt_i < len(row):
                amount = _parse_amount(row[amt_i])
            else:
                deb = (
                    _parse_amount(row[debit_i])
                    if debit_i is not None and debit_i < len(row)
                    else None
                )
                cred = (
                    _parse_amount(row[credit_i])
                    if credit_i is not None and credit_i < len(row)
                    else None
                )
                if deb and deb != 0:
                    amount = -abs(deb)
                elif cred and cred != 0:
                    amount = abs(cred)

            # Balance column: sample on every dated row (even if amount is bad)
            if bal_i is not None and bal_i < len(row):
                bal = _parse_amount(row[bal_i])
                if bal is not None:
                    bal_pairs.append((d, bal))
                    bal_amounts.append(amount)

            if amount is None or amount == 0:
                result.skipped_bad += 1
                continue

            if amount_sign == "invert":
                amount = -amount
            # Credit: charges → negative; payments/refunds → positive (after optional invert)
            if acct.kind == "credit" and amount_sign in ("bank", "invert"):
                from financial_os.services.import_amounts import normalize_credit_import_amount

                amount = normalize_credit_import_amount(amount, payee)

            bank_txn_id = ""
            if id_i is not None and id_i < len(row):
                bank_txn_id = (row[id_i] or "").strip()
            if bank_txn_id:
                external_id = f"csv:{account_id}:id:{bank_txn_id}"[:200]
            else:
                external_id = f"csv:{account_id}:{d.isoformat()}:{amount}:{payee[:80]}"
            if external_id in existing:
                result.skipped_existing += 1
                continue

            session.add(
                Transaction(
                    profile_id=acct.profile_id,
                    account_id=acct.id,
                    txn_date=d,
                    amount=amount,
                    payee=payee or None,
                    memo=f"CSV:{filename}",
                    status="cleared",
                    external_id=external_id,
                )
            )
            from financial_os.services.account_balance import apply_amount_to_account

            apply_amount_to_account(acct, amount)
            existing.add(external_id)
            result.transactions_created += 1
        except Exception as e:
            result.skipped_bad += 1
            if len(result.errors) < 5:
                result.errors.append(str(e))

    session.flush()

    # Ending balance → institution_balance (max txn date + amount orientation)
    column_ending = ending_balance_from_pairs(bal_pairs, bal_amounts)
    bal_to_apply: Decimal | None = None
    bal_source: str | None = None
    if institution_balance is not None:
        bal_to_apply = institution_balance
        bal_source = "override"
    elif apply_ending_balance and column_ending is not None:
        bal_to_apply = column_ending
        bal_source = "column"

    drift: Decimal | None = None
    if bal_to_apply is not None:
        from financial_os.services.reconcile import set_institution_balance

        set_institution_balance(session, account_id, bal_to_apply, mark_reconciled=False)
        result.institution_balance_set = True
        result.ending_balance = str(bal_to_apply)
        result.balance_source = bal_source
        session.refresh(acct)
        books = Decimal(str(acct.current_balance or 0))
        result.books_balance = str(books)
        drift = (books - bal_to_apply).quantize(Decimal("0.01"))
        result.drift = str(drift)
    else:
        result.books_balance = str(acct.current_balance or 0)
        if column_ending is not None:
            result.ending_balance = str(column_ending)
            result.balance_source = "column"
        else:
            result.balance_source = "none"

    if (
        result.transactions_created > 0
        or result.rows_scanned > 0
        or result.institution_balance_set
    ):
        # Any completed import run resets the freeware download reminder clock
        try:
            from financial_os.services.import_reminders import mark_import_activity

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
        result.categorized = sum(1 for r in applied if r.get("applied"))

    from financial_os.services.import_brief import build_post_import_next_steps

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
        source="CSV",
    )
    return result
