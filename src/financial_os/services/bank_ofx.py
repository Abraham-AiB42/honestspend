"""Import bank OFX / Quicken QFX transaction downloads (freeware path).

Parses SGML-style OFX 1.x and simple XML OFX 2.x without extra packages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from sqlalchemy.orm import Session

from financial_os.db import Account, Transaction
from financial_os.services.categorizer import categorize_uncategorized
from financial_os.services.import_reminders import mark_import_activity

_TAG_RE = re.compile(r"<(/?)([A-Za-z0-9._]+)\s*>")
_OPEN_VAL_RE = re.compile(r"<([A-Za-z0-9._]+)>([^<\r\n]*)")


@dataclass
class OfxImportResult:
    transactions_found: int = 0
    transactions_created: int = 0
    skipped_existing: int = 0
    skipped_bad: int = 0
    errors: list[str] = field(default_factory=list)
    categorized: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)
    account_hint: str | None = None
    bank_id: str | None = None
    ledger_balance: str | None = None
    ledger_balance_as_of: str | None = None
    institution_balance_set: bool = False
    books_balance: str | None = None
    drift: str | None = None
    next_steps: list[dict[str, str]] = field(default_factory=list)


def _read_text(file_obj: BinaryIO | TextIO | bytes | str | Path) -> str:
    if isinstance(file_obj, (str, Path)):
        raw = Path(file_obj).read_bytes()
    elif isinstance(file_obj, bytes):
        raw = file_obj
    else:
        data = file_obj.read()
        raw = data if isinstance(data, bytes) else str(data).encode("utf-8", errors="replace")
    # strip BOM / common encodings
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_ofx_date(raw: str) -> date | None:
    """OFX dates: YYYYMMDD or YYYYMMDDHHMMSS[.XXX][±tz]."""
    s = (raw or "").strip()
    if not s:
        return None
    # strip timezone suffix like [-5:EST] or [0:GMT]
    s = re.sub(r"\[.*$", "", s).strip()
    s = s.split(".")[0]
    for n in (14, 12, 8):
        if len(s) >= n:
            chunk = s[:n]
            try:
                if n == 8:
                    return datetime.strptime(chunk, "%Y%m%d").date()
                if n == 12:
                    return datetime.strptime(chunk, "%Y%m%d%H%M").date()
                return datetime.strptime(chunk, "%Y%m%d%H%M%S").date()
            except ValueError:
                continue
    return None


def _parse_amount(raw: str) -> Decimal | None:
    s = (raw or "").replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def parse_ofx_ledger_balance(text: str) -> dict[str, Any]:
    """Extract LEDGERBAL / AVAILBAL BALAMT (+ optional DTASOF)."""
    out: dict[str, Any] = {"balance": None, "as_of": None, "source": None}
    # Prefer LEDGERBAL block, then AVAILBAL, then any BALAMT
    for source, pattern in (
        ("ledger", r"<LEDGERBAL>(.*?)</LEDGERBAL>"),
        ("ledger", r"<LEDGERBAL>(.*?)(?=<AVAILBAL>|<BANKACCTFROM>|<CCACCTFROM>|</STMTRS>|</CCSTMTRS>|</OFX>)"),
        ("available", r"<AVAILBAL>(.*?)</AVAILBAL>"),
        ("available", r"<AVAILBAL>(.*?)(?=<LEDGERBAL>|<BANKACCTFROM>|</STMTRS>|</OFX>)"),
    ):
        m = re.search(pattern, text, re.I | re.S)
        if not m:
            continue
        block = m.group(1)
        bal_m = re.search(r"<BALAMT>([^<\r\n]+)", block, re.I)
        if not bal_m:
            continue
        bal = _parse_amount(bal_m.group(1))
        if bal is None:
            continue
        out["balance"] = bal
        out["source"] = source
        dt_m = re.search(r"<DTASOF>([^<\r\n]+)", block, re.I)
        if dt_m:
            d = _parse_ofx_date(dt_m.group(1))
            if d:
                out["as_of"] = d
        return out
    # bare BALAMT (some exporters)
    bare = re.search(r"<BALAMT>([^<\r\n]+)", text, re.I)
    if bare:
        bal = _parse_amount(bare.group(1))
        if bal is not None:
            out["balance"] = bal
            out["source"] = "balamt"
    return out


def parse_ofx_transactions(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Extract STMTTRN (and similar) blocks. Returns (rows, meta)."""
    meta: dict[str, str] = {}
    # account hints
    for key in ("ACCTID", "BANKID", "ORG", "FID", "ACCTTYPE"):
        m = re.search(rf"<{key}>([^<\r\n]+)", text, re.I)
        if m:
            meta[key.lower()] = m.group(1).strip()

    ledger = parse_ofx_ledger_balance(text)
    if ledger.get("balance") is not None:
        meta["ledger_balance"] = str(ledger["balance"])
        meta["ledger_source"] = str(ledger.get("source") or "ledger")
        if ledger.get("as_of") is not None:
            meta["ledger_as_of"] = ledger["as_of"].isoformat()

    rows: list[dict[str, Any]] = []
    # Split on STMTTRN open (handles both <STMTTRN>…</STMTTRN> and SGML style)
    parts = re.split(r"<STMTTRN>", text, flags=re.I)
    for part in parts[1:]:
        # end at next closing or next major tag
        block = re.split(r"</STMTTRN>|<STMTTRN>", part, maxsplit=1, flags=re.I)[0]
        fields: dict[str, str] = {}
        for m in _OPEN_VAL_RE.finditer(block):
            tag = m.group(1).upper()
            val = m.group(2).strip()
            if val:
                fields[tag] = val
        # also XML-style <TAG>value</TAG>
        for m in re.finditer(r"<([A-Za-z0-9._]+)>([^<]*)</\1>", block, re.I):
            fields[m.group(1).upper()] = m.group(2).strip()

        amt = _parse_amount(fields.get("TRNAMT", ""))
        d = _parse_ofx_date(fields.get("DTPOSTED", "") or fields.get("DTUSER", ""))
        if amt is None or d is None:
            continue
        name = fields.get("NAME") or fields.get("PAYEE") or fields.get("MEMO") or ""
        memo = fields.get("MEMO") or ""
        if name and memo and memo.lower() != name.lower():
            payee = f"{name} · {memo}"[:256]
        else:
            payee = (name or memo or "OFX transaction")[:256]
        fitid = fields.get("FITID") or fields.get("REFNUM") or ""
        trntype = (fields.get("TRNTYPE") or "").upper()
        rows.append(
            {
                "txn_date": d,
                "amount": amt,
                "payee": payee,
                "memo": memo[:200] if memo else None,
                "fitid": fitid,
                "trntype": trntype,
            }
        )
    return rows, meta


def preview_ofx(file_obj: BinaryIO | TextIO | bytes | str | Path) -> dict[str, Any]:
    text = _read_text(file_obj)
    rows, meta = parse_ofx_transactions(text)
    ledger = parse_ofx_ledger_balance(text)
    bal = ledger.get("balance")
    return {
        "ok": len(rows) > 0 or bal is not None,
        "transactions_found": len(rows),
        "account_hint": meta.get("acctid"),
        "bank_id": meta.get("bankid"),
        "org": meta.get("org"),
        "ledger_balance": str(bal) if bal is not None else None,
        "ledger_balance_as_of": meta.get("ledger_as_of"),
        "ledger_source": ledger.get("source"),
        "sample": [
            {
                "txn_date": r["txn_date"].isoformat(),
                "payee": r["payee"],
                "amount": str(r["amount"]),
                "fitid": r.get("fitid") or "",
            }
            for r in rows[:12]
        ],
        "hint": (
            (
                "OFX/QFX ready — import maps TRNAMT; LEDGERBAL sets institution balance for Reconcile."
                if bal is not None
                else "OFX/QFX ready — import maps TRNAMT (bank sign: expenses usually negative)."
            )
            if rows
            else (
                "Found ledger balance but no STMTTRN rows."
                if bal is not None
                else "No STMTTRN blocks found. Prefer CSV if this bank export is empty."
            )
        ),
    }


def import_ofx(
    session: Session,
    *,
    account_id: int,
    file_obj: BinaryIO | TextIO | bytes | str | Path,
    filename: str = "download.ofx",
    auto_categorize: bool = True,
    amount_sign: str = "bank",
    apply_ledger_balance: bool = True,
) -> OfxImportResult:
    result = OfxImportResult()
    acct = session.get(Account, account_id)
    if not acct:
        result.errors.append("Account not found")
        return result

    try:
        text = _read_text(file_obj)
        rows, meta = parse_ofx_transactions(text)
        ledger = parse_ofx_ledger_balance(text)
    except Exception as e:
        result.errors.append(str(e))
        return result

    result.transactions_found = len(rows)
    result.account_hint = meta.get("acctid")
    result.bank_id = meta.get("bankid")
    if ledger.get("balance") is not None:
        result.ledger_balance = str(ledger["balance"])
        if ledger.get("as_of") is not None:
            result.ledger_balance_as_of = ledger["as_of"].isoformat()
    result.sample = [
        {
            "txn_date": r["txn_date"].isoformat(),
            "payee": r["payee"],
            "amount": str(r["amount"]),
        }
        for r in rows[:8]
    ]
    if not rows and ledger.get("balance") is None:
        result.errors.append("No transactions found in OFX/QFX file")
        return result

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
        # OFX credit-card convention varies; charges often negative already in bank OFX
        payee = r["payee"]
        d = r["txn_date"]
        fitid = (r.get("fitid") or "").strip()
        if fitid:
            external_id = f"ofx:{account_id}:{fitid}"[:200]
        else:
            external_id = f"ofx:{account_id}:{d.isoformat()}:{amt}:{payee[:60]}"[:200]
        if external_id in existing:
            result.skipped_existing += 1
            continue
        try:
            session.add(
                Transaction(
                    profile_id=acct.profile_id,
                    account_id=acct.id,
                    txn_date=d,
                    amount=amt,
                    payee=payee or None,
                    memo=(r.get("memo") or f"OFX:{filename}")[:256],
                    status="cleared",
                    external_id=external_id,
                )
            )
            existing.add(external_id)
            result.transactions_created += 1
        except Exception as e:
            result.skipped_bad += 1
            if len(result.errors) < 5:
                result.errors.append(str(e))

    session.flush()

    # LEDGERBAL → institution_balance (does not overwrite books current_balance)
    drift: Decimal | None = None
    if apply_ledger_balance and ledger.get("balance") is not None:
        from financial_os.services.reconcile import set_institution_balance

        bal = ledger["balance"]
        assert isinstance(bal, Decimal)
        set_institution_balance(session, account_id, bal, mark_reconciled=False)
        result.institution_balance_set = True
        session.refresh(acct)
        books = Decimal(str(acct.current_balance or 0))
        result.books_balance = str(books)
        drift = (books - bal).quantize(Decimal("0.01"))
        result.drift = str(drift)
    else:
        result.books_balance = str(acct.current_balance or 0)

    if result.transactions_created or result.skipped_existing or result.institution_balance_set:
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

    from financial_os.services.import_brief import build_post_import_next_steps

    result.next_steps = build_post_import_next_steps(
        session,
        profile_id=acct.profile_id,
        created=result.transactions_created,
        categorized=result.categorized,
        drift=drift,
        source="OFX/QFX",
    )
    return result
