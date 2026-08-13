"""Parse issuer activity workbooks (Amex activity.xlsx and siblings).

These are not the legacy Budget.xlsx daily ledger. Banks download them as
activity.xlsx / activity (1).xlsx — product name and account mask live in
the first rows, transactions start at a Date/Description/Amount header.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Transaction


def _as_date(val: Any) -> date | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s[:10]).date()
    except ValueError:
        return None


def _as_decimal(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    s = str(val).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _cell_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def looks_like_activity_xlsx(rows: list[tuple[Any, ...]] | None = None, sheet_names: list[str] | None = None) -> bool:
    names = " ".join(sheet_names or []).lower()
    if "transaction details" in names or "transaction summary" in names:
        return True
    blob = " ".join(_cell_str(c) for r in (rows or [])[:12] for c in r[:4]).lower()
    if "account number" in blob and re.search(r"x{2,}", blob):
        return True
    if re.search(r"\b(blue business|hilton honors|amazon business|gold card|platinum card)\b", blob):
        return True
    return False


def _header_indexes(row: tuple[Any, ...]) -> dict[str, int] | None:
    norms = [_cell_str(c).strip().lower() for c in row]
    if "date" not in norms or "amount" not in norms:
        return None
    if not any(h in ("description", "payee", "name") for h in norms):
        return None
    idx: dict[str, int] = {}
    for i, h in enumerate(norms):
        if h == "date" and "date" not in idx:
            idx["date"] = i
        elif h in ("description", "payee", "name") and "desc" not in idx:
            idx["desc"] = i
        elif h == "amount" and "amount" not in idx:
            idx["amount"] = i
        elif h == "category" and "category" not in idx:
            idx["category"] = i
        elif h == "reference" and "reference" not in idx:
            idx["reference"] = i
    if "date" in idx and "desc" in idx and "amount" in idx:
        return idx
    return None


def parse_activity_xlsx(content: bytes, filename: str = "activity.xlsx") -> dict[str, Any] | None:
    """Return one account + rows, or None if this is not an issuer activity book."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return None
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        return None

    try:
        sheet_names = list(wb.sheetnames)
        details = None
        summary = None
        for name in sheet_names:
            low = name.lower()
            if low == "transaction details" or (details is None and "details" in low):
                details = wb[name]
            if low == "transaction summary" or (summary is None and "summary" in low):
                summary = wb[name]
        if details is None:
            details = wb[sheet_names[0]]

        raw_rows: list[tuple[Any, ...]] = []
        for row in details.iter_rows(values_only=True):
            raw_rows.append(tuple(row))
            if len(raw_rows) > 8000:
                break

        if not looks_like_activity_xlsx(raw_rows, sheet_names):
            return None

        product = ""
        cardholder = ""
        acct_mask = ""
        header_i = None
        cols = None
        for i, row in enumerate(raw_rows[:40]):
            cells = [_cell_str(c) for c in row]
            joined = " ".join(cells)
            if i == 0 and len(cells) > 1 and cells[1]:
                product = cells[1].split("/")[0].strip()
            if cells and cells[0].lower() == "prepared for" and i + 1 < len(raw_rows):
                cardholder = _cell_str(raw_rows[i + 1][0])
            if cells and cells[0].lower() == "account number" and i + 1 < len(raw_rows):
                acct_mask = _cell_str(raw_rows[i + 1][0])
            found = _header_indexes(row)
            if found:
                header_i = i
                cols = found
                break
            # Product sometimes only in col B of title row
            if not product:
                for c in cells[1:3]:
                    if c and len(c) > 8 and "card" in c.lower():
                        product = c.split("/")[0].strip()
                        break

        # Summary sheet: product / mask / ending balance
        ending = None
        if summary is not None:
            srows: list[tuple[Any, ...]] = []
            for row in summary.iter_rows(values_only=True):
                srows.append(tuple(row))
                if len(srows) > 40:
                    break
            for i, row in enumerate(srows):
                cells = [_cell_str(c) for c in row]
                if not product and len(cells) > 1 and "card" in cells[1].lower():
                    product = cells[1].split("/")[0].strip()
                if cells and cells[0].lower() == "account number" and i + 1 < len(srows):
                    acct_mask = acct_mask or _cell_str(srows[i + 1][0])
                if cells and cells[0].lower() in ("total balance", "new balance"):
                    for c in row[1:]:
                        d = _as_decimal(c)
                        if d is not None:
                            ending = d
                            break

        last4 = None
        m = re.search(r"(\d{4,5})\s*$", acct_mask.replace(" ", ""))
        if m:
            last4 = m.group(1)[-4:]
            acct_tail = m.group(1)
        else:
            acct_tail = None

        rows: list[dict[str, Any]] = []
        categories: list[str] = []
        if header_i is not None and cols is not None:
            for row in raw_rows[header_i + 1 :]:
                if cols["date"] >= len(row):
                    continue
                d = _as_date(row[cols["date"]])
                if not d:
                    continue
                payee = _cell_str(row[cols["desc"]]) if cols["desc"] < len(row) else ""
                amt = _as_decimal(row[cols["amount"]]) if cols["amount"] < len(row) else None
                if amt is None:
                    continue
                cat = ""
                if "category" in cols and cols["category"] < len(row):
                    cat = _cell_str(row[cols["category"]])
                    if cat:
                        categories.append(cat)
                ref = ""
                if "reference" in cols and cols["reference"] < len(row):
                    ref = _cell_str(row[cols["reference"]])
                rows.append(
                    {
                        "txn_date": d,
                        "payee": payee,
                        "amount": amt,
                        "category": cat,
                        "bank_txn_id": ref,
                    }
                )

        dates = [r["txn_date"] for r in rows]
        prod_l = product.lower()
        is_biz = bool(
            re.search(
                r"\bbusiness\b|\bamazon business\b|\bblue business\b",
                prod_l,
            )
        )
        return {
            "ok": True,
            "file_format": "xlsx",
            "kind": "credit",
            "org": "American Express",
            "product": product or None,
            "cardholder": cardholder or None,
            "acct_mask": acct_mask or None,
            "acctid": acct_tail or last4,
            "last4": last4,
            "suggested_entity_type": "business" if is_biz else "personal",
            "transactions_found": len(rows),
            "ledger_balance": str(ending) if ending is not None else None,
            "date_from": min(dates).isoformat() if dates else None,
            "date_to": max(dates).isoformat() if dates else None,
            "sample_payees": [r["payee"] for r in rows[:8] if r.get("payee")],
            "sample_categories": categories[:12],
            "rows": rows,
            "filename": filename,
        }
    finally:
        try:
            wb.close()
        except Exception:
            pass


@dataclass
class ActivityXlsxImportResult:
    rows_scanned: int = 0
    transactions_created: int = 0
    skipped_existing: int = 0
    skipped_bad: int = 0
    errors: list[str] = field(default_factory=list)
    ending_balance: str | None = None


def import_activity_xlsx(
    session: Session,
    *,
    account_id: int,
    content: bytes,
    filename: str,
) -> ActivityXlsxImportResult:
    """Write parsed issuer activity rows onto an existing account."""
    result = ActivityXlsxImportResult()
    parsed = parse_activity_xlsx(content, filename)
    if not parsed:
        result.errors.append("Not an issuer activity workbook")
        return result
    acct = session.get(Account, account_id)
    if acct is None:
        result.errors.append("Account not found")
        return result

    from honestspend.services.account_balance import apply_amount_to_account
    from honestspend.services.import_amounts import normalize_credit_import_amount
    from honestspend.services.import_dedupe import load_dedupe_index
    from honestspend.services.import_txn_review import find_existing_by_external_id

    dedupe = load_dedupe_index(session, account_id)
    for row in parsed.get("rows") or []:
        result.rows_scanned += 1
        d = row.get("txn_date")
        payee = (row.get("payee") or "").strip()
        amt = row.get("amount")
        if not d or amt is None:
            result.skipped_bad += 1
            continue
        amount = amt if isinstance(amt, Decimal) else Decimal(str(amt))
        if acct.kind == "credit":
            amount = normalize_credit_import_amount(amount, payee)
        bank_txn_id = (row.get("bank_txn_id") or "").strip()
        external_id = dedupe.preferred_external_id(
            txn_date=d,
            amount=amount,
            payee=payee,
            bank_txn_id=bank_txn_id or None,
            source="xlsx",
        )
        existing = find_existing_by_external_id(session, account_id, external_id)
        if existing is not None:
            result.skipped_existing += 1
            continue
        if dedupe.is_duplicate(
            txn_date=d,
            amount=amount,
            payee=payee,
            bank_txn_id=bank_txn_id or None,
        ):
            result.skipped_existing += 1
            continue
        session.add(
            Transaction(
                profile_id=acct.profile_id,
                account_id=acct.id,
                txn_date=d,
                amount=amount,
                payee=payee or None,
                memo=f"XLSX:{filename}",
                status="cleared",
                external_id=external_id,
            )
        )
        apply_amount_to_account(acct, amount)
        dedupe.remember(
            txn_date=d,
            amount=amount,
            payee=payee,
            bank_txn_id=bank_txn_id or None,
            external_id=external_id,
        )
        result.transactions_created += 1

    if parsed.get("ledger_balance"):
        try:
            from honestspend.services.reconcile import set_institution_balance

            bal = Decimal(str(parsed["ledger_balance"]))
            set_institution_balance(session, account_id, bal, mark_reconciled=False)
            result.ending_balance = str(bal)
        except Exception:
            pass
    session.flush()
    try:
        from honestspend.services.import_reminders import mark_import_activity

        mark_import_activity(session)
    except Exception:
        pass
    return result
