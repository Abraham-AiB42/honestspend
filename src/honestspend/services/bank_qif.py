"""Import Quicken Interchange Format (QIF) bank / card downloads.

Supports classic !Type:Bank / !Type:CCard blocks and multi-account
!Account headers used by Quicken and many banks' "export QIF" options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from sqlalchemy.orm import Session

from honestspend.db import Account, Transaction
from honestspend.services.categorizer import categorize_uncategorized
from honestspend.services.import_reminders import mark_import_activity

_DATE_FMTS = (
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%m.%d.%Y",
    "%d.%m.%Y",
)


@dataclass
class QifImportResult:
    transactions_found: int = 0
    transactions_created: int = 0
    skipped_existing: int = 0
    skipped_bad: int = 0
    errors: list[str] = field(default_factory=list)
    categorized: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)
    account_hint: str | None = None
    institution_balance_set: bool = False
    books_balance: str | None = None
    drift: str | None = None
    next_steps: list[dict[str, str]] = field(default_factory=list)
    schedules_advanced: int = 0
    schedules_advanced_names: list[str] = field(default_factory=list)
    schedule_advance_hint: str | None = None
    schedule_advance_error: str | None = None


def _read_text(file_obj: BinaryIO | TextIO | bytes | str | Path) -> str:
    if isinstance(file_obj, (str, Path)):
        raw = Path(file_obj).read_bytes()
    elif isinstance(file_obj, bytes):
        raw = file_obj
    else:
        data = file_obj.read()
        raw = data if isinstance(data, bytes) else str(data).encode("utf-8", errors="replace")
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_qif_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    # Quicken apostrophe form: 1/ 5'26 or 1/15'26
    s = s.replace("'", "/")
    s = re.sub(r"\s+", "", s)
    # Strip trailing time if present
    s = s.split(" ")[0].split("T")[0]
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last resort: loose MM/DD/YYYY with flexible zeros
    m = re.match(r"^(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})$", s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        try:
            if len(a) == 4:
                return date(int(a), int(b), int(c))
            if len(c) == 4:
                return date(int(c), int(a), int(b))
            yy = int(c)
            year = 2000 + yy if yy < 70 else 1900 + yy
            return date(year, int(a), int(b))
        except ValueError:
            pass
    return None


def _parse_amount(raw: str) -> Decimal | None:
    s = (raw or "").strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").replace("$", "").replace(" ", "").replace("\xa0", "")
    if s.endswith("-"):
        neg = True
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    try:
        val = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -val if neg else val


def _map_qif_type(type_token: str | None) -> str:
    t = (type_token or "Bank").strip().lower()
    if t in ("ccard", "c card", "credit card", "creditcard", "credit"):
        return "credit"
    if t in ("cash",):
        return "cash"
    if t in ("oth a", "otha", "asset", "oth l", "othl", "liability"):
        return "checking"
    if t in ("invst", "investment", "mutual"):
        return "checking"  # investment detail not modeled — import as cash-like
    if t in ("bank", "checking", "sav", "savings"):
        if "sav" in t:
            return "savings"
        return "checking"
    return "checking"


def parse_qif_accounts(text: str) -> list[dict[str, Any]]:
    """Split a QIF into account statements with transaction rows.

    Each item: name, kind, rows[{txn_date, amount, payee, memo, check_num}],
    transactions_found.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    accounts: list[dict[str, Any]] = []
    current_name: str | None = None
    current_kind = "checking"
    current_rows: list[dict[str, Any]] = []
    in_account_header = False
    header_name: str | None = None
    header_type: str | None = None
    pending: dict[str, str] = {}

    def flush_txn() -> None:
        nonlocal pending
        if not pending:
            return
        d = _parse_qif_date(pending.get("D", ""))
        amt = _parse_amount(pending.get("T", "") or pending.get("U", ""))
        payee = (pending.get("P") or pending.get("M") or "").strip()
        memo = (pending.get("M") or "").strip()
        if pending.get("P") and pending.get("M") and pending["P"].strip() == pending["M"].strip():
            memo = ""
        check_num = (pending.get("N") or "").strip()
        # Category L often useful as memo fallback
        cat = (pending.get("L") or "").strip()
        if cat and not memo:
            memo = cat
        pending = {}
        if d is None or amt is None:
            return
        if not payee:
            payee = memo or (f"Check {check_num}" if check_num else "QIF transfer")
        current_rows.append(
            {
                "txn_date": d,
                "amount": amt,
                "payee": payee[:200],
                "memo": (memo or "")[:256],
                "check_num": check_num or None,
            }
        )

    def flush_account() -> None:
        nonlocal current_rows, current_name, current_kind, pending
        flush_txn()
        # Only emit accounts that actually have transactions (avoid empty !Account shells)
        if current_rows:
            name = (current_name or "QIF account").strip() or "QIF account"
            accounts.append(
                {
                    "name": name,
                    "kind": current_kind,
                    "rows": list(current_rows),
                    "transactions_found": len(current_rows),
                    "external_key": f"qif:{(name or 'acct').lower()[:60]}",
                    "acctid": name,
                }
            )
        current_rows = []
        pending = {}

    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("!Option:") or line.startswith("!Clear:"):
            continue
        if line.startswith("!Account"):
            flush_account()
            in_account_header = True
            header_name = None
            header_type = None
            continue
        if line.startswith("!Type:"):
            # Starting a new type block — flush prior *transactions*, keep/apply name
            flush_account()
            type_token = line[6:].strip()
            current_kind = _map_qif_type(type_token)
            if header_name:
                current_name = header_name
                if header_type:
                    current_kind = _map_qif_type(header_type)
            elif not current_name:
                current_name = type_token or "Bank"
            in_account_header = False
            header_name = None
            header_type = None
            continue
        if line.startswith("!"):
            flush_account()
            in_account_header = False
            continue

        code = line[0]
        val = line[1:] if len(line) > 1 else ""

        if in_account_header:
            if code == "N":
                header_name = val.strip()
            elif code == "T":
                header_type = val.strip()
            elif code == "^":
                current_name = header_name or current_name
                if header_type:
                    current_kind = _map_qif_type(header_type)
                in_account_header = False
            continue

        if code == "^":
            flush_txn()
            continue
        # Accumulate transaction fields (first letter is field code)
        if code in ("D", "T", "U", "P", "M", "N", "L", "C", "A", "S", "E", "$", "F", "Y"):
            if code == "S" and "L" not in pending:
                pending["L"] = val
            elif code == "$" and "T" not in pending:
                pending["T"] = val
            elif code not in ("S", "E", "$", "A", "F", "Y", "C"):
                if code not in pending or not pending[code]:
                    pending[code] = val
                elif code in ("M", "P") and val and val not in pending[code]:
                    pending[code] = f"{pending[code]} {val}".strip()
            continue

    flush_account()
    return accounts


def parse_qif_transactions(text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Flatten first (or only) account — for single-target import."""
    accounts = parse_qif_accounts(text)
    if not accounts:
        return [], {}
    # Merge all accounts' rows when caller uses single account_id (common)
    rows: list[dict[str, Any]] = []
    for a in accounts:
        rows.extend(a.get("rows") or [])
    meta = {
        "account_name": accounts[0].get("name"),
        "account_count": len(accounts),
        "kind": accounts[0].get("kind"),
        "accounts": [
            {
                "name": a.get("name"),
                "kind": a.get("kind"),
                "transactions_found": a.get("transactions_found"),
            }
            for a in accounts
        ],
    }
    return rows, meta


def preview_qif(file_obj: BinaryIO | TextIO | bytes | str | Path) -> dict[str, Any]:
    try:
        text = _read_text(file_obj)
        accounts = parse_qif_accounts(text)
        rows, meta = parse_qif_transactions(text)
    except Exception as e:
        return {"ok": False, "error": str(e), "transactions_found": 0}
    sample = [
        {
            "txn_date": r["txn_date"].isoformat(),
            "payee": r["payee"],
            "amount": str(r["amount"]),
        }
        for r in rows[:8]
    ]
    return {
        "ok": True,
        "transactions_found": len(rows),
        "account_count": len(accounts),
        "account_hint": meta.get("account_name"),
        "accounts": meta.get("accounts") or [],
        "sample": sample,
        "hint": (
            f"QIF · {len(rows)} transactions"
            + (f" · {len(accounts)} accounts" if len(accounts) > 1 else "")
        ),
    }


def import_qif(
    session: Session,
    *,
    account_id: int,
    file_obj: BinaryIO | TextIO | bytes | str | Path,
    filename: str = "download.qif",
    auto_categorize: bool = True,
    amount_sign: str = "bank",
) -> QifImportResult:
    result = QifImportResult()
    acct = session.get(Account, account_id)
    if not acct:
        result.errors.append("Account not found")
        return result

    try:
        text = _read_text(file_obj)
        rows, meta = parse_qif_transactions(text)
    except Exception as e:
        result.errors.append(str(e))
        return result

    result.transactions_found = len(rows)
    result.account_hint = meta.get("account_name")
    result.sample = [
        {
            "txn_date": r["txn_date"].isoformat(),
            "payee": r["payee"],
            "amount": str(r["amount"]),
        }
        for r in rows[:8]
    ]
    if not rows:
        result.errors.append("No transactions found in QIF file")
        return result

    from honestspend.services.import_dedupe import load_dedupe_index

    dedupe = load_dedupe_index(session, account_id)

    for r in rows:
        amt = r["amount"]
        payee = r["payee"]
        if amount_sign == "invert":
            amt = -amt
        if acct.kind == "credit" and amount_sign in ("bank", "invert"):
            from honestspend.services.import_amounts import normalize_credit_import_amount

            amt = normalize_credit_import_amount(amt, payee)
        d = r["txn_date"]
        if dedupe.is_duplicate(txn_date=d, amount=amt, payee=payee, fitid=None):
            result.skipped_existing += 1
            continue
        external_id = dedupe.preferred_external_id(
            txn_date=d,
            amount=amt,
            payee=payee,
            fitid=None,
            source="qif",
        )
        try:
            memo = (r.get("memo") or f"QIF:{filename}")[:256]
            session.add(
                Transaction(
                    profile_id=acct.profile_id,
                    account_id=acct.id,
                    txn_date=d,
                    amount=amt,
                    payee=payee or None,
                    memo=memo,
                    status="cleared",
                    external_id=external_id,
                )
            )
            from honestspend.services.account_balance import apply_amount_to_account

            apply_amount_to_account(acct, amt)
            dedupe.remember(
                txn_date=d,
                amount=amt,
                payee=payee,
                fitid=None,
                external_id=external_id,
            )
            result.transactions_created += 1
        except Exception as e:
            result.skipped_bad += 1
            if len(result.errors) < 5:
                result.errors.append(str(e))

    session.flush()
    result.books_balance = str(acct.current_balance or 0)

    if result.transactions_created or result.skipped_existing:
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
            result.schedule_advance_error = str(e)[:300]

    from honestspend.services.import_brief import build_post_import_next_steps

    result.next_steps = build_post_import_next_steps(
        session,
        profile_id=acct.profile_id,
        account_id=account_id,
        created=result.transactions_created,
        categorized=result.categorized,
        skipped_existing=result.skipped_existing,
        drift=None,
        books_balance=result.books_balance,
        institution_balance=None,
        source="QIF",
    )
    return result


def import_qif_multi(
    session: Session,
    *,
    file_obj: BinaryIO | TextIO | bytes | str | Path,
    filename: str = "download.qif",
    auto_categorize: bool = True,
    amount_sign: str = "bank",
    auto_create_accounts: bool = True,
    profile_id: int | None = None,
    account_map: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Import multi-account QIF (!Account sections)."""
    from honestspend.db import Profile

    text = _read_text(file_obj)
    stmts = parse_qif_accounts(text)
    if not stmts:
        return {
            "ok": False,
            "error": "No QIF accounts/transactions found",
            "transactions_created": 0,
            "accounts": [],
        }

    if profile_id is None:
        prof = session.query(Profile).order_by(Profile.id.asc()).first()
        profile_id = int(prof.id) if prof else None
    if profile_id is None:
        return {"ok": False, "error": "No profile — run setup first", "accounts": []}

    account_map = dict(account_map or {})
    out_accounts: list[dict[str, Any]] = []
    total_created = 0
    total_found = 0
    total_skipped = 0

    for stmt in stmts:
        name = (stmt.get("name") or "QIF").strip()
        sk = stmt.get("external_key") or f"qif:{name.lower()}"
        rows = stmt.get("rows") or []
        total_found += len(rows)
        target_id = account_map.get(name) or account_map.get(sk)
        acct: Account | None = None
        if target_id:
            acct = session.get(Account, int(target_id))
        if acct is None:
            # match by nickname / external
            hit = (
                session.query(Account)
                .filter(Account.external_id == sk)
                .order_by(Account.id.asc())
                .first()
            )
            if hit is None:
                low = name.lower()
                for a in session.query(Account).filter(Account.archived_at.is_(None)).all():
                    if low and low in (a.nickname or "").lower():
                        hit = a
                        break
            acct = hit
        if acct is None and auto_create_accounts:
            kind = stmt.get("kind") or "checking"
            if kind not in ("checking", "savings", "credit", "cash"):
                kind = "checking"
            acct = Account(
                profile_id=profile_id,
                kind=kind,
                nickname=name[:80],
                current_balance=Decimal("0"),
                external_id=sk,
                is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
            )
            session.add(acct)
            session.flush()
        if acct is None:
            out_accounts.append(
                {
                    "name": name,
                    "ok": False,
                    "error": "No matching account (auto-create off)",
                    "transactions_found": len(rows),
                }
            )
            continue

        # Import rows for this statement only (write temp single-account QIF body)
        body_lines = [f"!Type:{'CCard' if acct.kind == 'credit' else 'Bank'}"]
        for r in rows:
            body_lines.append(f"D{r['txn_date'].strftime('%m/%d/%Y')}")
            body_lines.append(f"T{r['amount']}")
            body_lines.append(f"P{r.get('payee') or ''}")
            if r.get("memo"):
                body_lines.append(f"M{r['memo']}")
            body_lines.append("^")
        single = "\n".join(body_lines) + "\n"
        res = import_qif(
            session,
            account_id=int(acct.id),
            file_obj=single.encode("utf-8"),
            filename=filename,
            auto_categorize=auto_categorize,
            amount_sign=amount_sign,
        )
        total_created += res.transactions_created
        total_skipped += res.skipped_existing
        out_accounts.append(
            {
                "name": name,
                "nickname": acct.nickname,
                "account_id": acct.id,
                "kind": acct.kind,
                "ok": True,
                "transactions_found": res.transactions_found,
                "transactions_created": res.transactions_created,
                "skipped_existing": res.skipped_existing,
                "errors": res.errors[:3],
            }
        )

    return {
        "ok": True,
        "format": "qif",
        "filename": filename,
        "transactions_found": total_found,
        "transactions_created": total_created,
        "skipped_existing": total_skipped,
        "account_count": len(out_accounts),
        "accounts": out_accounts,
        "hint": f"QIF · {total_created} new · {total_skipped} dups · {len(out_accounts)} account(s)",
    }
