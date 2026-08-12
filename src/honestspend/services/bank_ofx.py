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

from honestspend.db import Account, Transaction
from honestspend.services.categorizer import categorize_uncategorized
from honestspend.services.import_reminders import mark_import_activity

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


def ofx_external_account_key(acctid: str | None) -> str | None:
    """Stable Account.external_id for bank ACCTID (enables inbox auto-match)."""
    if not acctid:
        return None
    digits = re.sub(r"\D+", "", str(acctid).strip())
    raw = (str(acctid).strip() or digits)[:64]
    if not raw:
        return None
    return f"ofx:{raw}"[:128]


def extract_ofx_acctid(text: str) -> str | None:
    m = re.search(r"<ACCTID>([^<\r\n]+)", text, re.I)
    if not m:
        return None
    return m.group(1).strip() or None


def peek_ofx_meta(file_obj: BinaryIO | TextIO | bytes | str | Path) -> dict[str, str]:
    """Lightweight account hints for matching before full import."""
    text = _read_text(file_obj)
    meta: dict[str, str] = {}
    for key in ("ACCTID", "BANKID", "ORG", "ACCTTYPE"):
        m = re.search(rf"<{key}>([^<\r\n]+)", text, re.I)
        if m:
            meta[key.lower()] = m.group(1).strip()
    if "acctid" in meta:
        key = ofx_external_account_key(meta["acctid"])
        if key:
            meta["external_key"] = key
    return meta


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


def _stmttrn_rows_from_block(block: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    parts = re.split(r"<STMTTRN>", block, flags=re.I)
    for part in parts[1:]:
        body = re.split(r"</STMTTRN>|<STMTTRN>", part, maxsplit=1, flags=re.I)[0]
        fields: dict[str, str] = {}
        for m in _OPEN_VAL_RE.finditer(body):
            tag = m.group(1).upper()
            val = m.group(2).strip()
            if val:
                fields[tag] = val
        for m in re.finditer(r"<([A-Za-z0-9._]+)>([^<]*)</\1>", body, re.I):
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
    return rows


def _meta_from_block(block: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for key in ("ACCTID", "BANKID", "ORG", "FID", "ACCTTYPE"):
        m = re.search(rf"<{key}>([^<\r\n]+)", block, re.I)
        if m:
            meta[key.lower()] = m.group(1).strip()
    ledger = parse_ofx_ledger_balance(block)
    if ledger.get("balance") is not None:
        meta["ledger_balance"] = str(ledger["balance"])
        meta["ledger_source"] = str(ledger.get("source") or "ledger")
        if ledger.get("as_of") is not None:
            meta["ledger_as_of"] = ledger["as_of"].isoformat()
    return meta


def split_ofx_statement_blocks(text: str) -> list[str]:
    """Split multi-account OFX into per-statement blocks (STMTRS / CCSTMTRS)."""
    blocks: list[str] = []
    for tag in ("STMTRS", "CCSTMTRS"):
        # Prefer closed XML-style blocks
        for m in re.finditer(rf"<{tag}\b[^>]*>(.*?)(?:</{tag}>)", text, re.I | re.S):
            blocks.append(m.group(0))
        if blocks:
            continue
        # SGML-style: open tag until next sibling or end
        for m in re.finditer(
            rf"<{tag}\b[^>]*>(.*?)(?=<{tag}\b|</BANKMSGSRSV1>|</CREDITCARDMSGSRSV1>|</OFX>|$)",
            text,
            re.I | re.S,
        ):
            blocks.append(m.group(0))
    if not blocks:
        blocks = [text]
    # Dedupe identical slices
    seen: set[str] = set()
    unique: list[str] = []
    for b in blocks:
        key = b[:200] + str(len(b))
        if key in seen:
            continue
        seen.add(key)
        unique.append(b)
    return unique


def parse_ofx_accounts(text: str) -> list[dict[str, Any]]:
    """Parse one OFX/QFX into zero-or-more bank accounts (Canvas-style multi-account files)."""
    accounts: list[dict[str, Any]] = []
    for block in split_ofx_statement_blocks(text):
        rows = _stmttrn_rows_from_block(block)
        meta = _meta_from_block(block)
        ledger = parse_ofx_ledger_balance(block)
        if not rows and ledger.get("balance") is None and not meta.get("acctid"):
            continue
        acct_type = (meta.get("accttype") or "").upper()
        kind = "credit" if ("CREDIT" in acct_type or "CC" in acct_type or "CREDITCARD" in block.upper()[:80]) else "checking"
        if "SAV" in acct_type:
            kind = "savings"
        if "MONEYMRKT" in acct_type or "MMA" in acct_type:
            kind = "savings"
        accounts.append(
            {
                "acctid": meta.get("acctid"),
                "bank_id": meta.get("bankid"),
                "org": meta.get("org"),
                "accttype": meta.get("accttype"),
                "kind": kind,
                "external_key": ofx_external_account_key(meta.get("acctid")),
                "transactions_found": len(rows),
                "ledger_balance": str(ledger["balance"]) if ledger.get("balance") is not None else None,
                "ledger_balance_as_of": meta.get("ledger_as_of"),
                "rows": rows,
                "meta": meta,
                "ledger": ledger,
                "block": block,
            }
        )
    # Fallback: whole-file parse if splitter found nothing useful
    if not accounts:
        rows, meta = parse_ofx_transactions(text)
        ledger = parse_ofx_ledger_balance(text)
        accounts.append(
            {
                "acctid": meta.get("acctid"),
                "bank_id": meta.get("bankid"),
                "org": meta.get("org"),
                "accttype": meta.get("accttype"),
                "kind": "checking",
                "external_key": ofx_external_account_key(meta.get("acctid")),
                "transactions_found": len(rows),
                "ledger_balance": str(ledger["balance"]) if ledger.get("balance") is not None else None,
                "ledger_balance_as_of": meta.get("ledger_as_of"),
                "rows": rows,
                "meta": meta,
                "ledger": ledger,
                "block": text,
            }
        )
    return accounts


def parse_ofx_transactions(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Extract STMTTRN (and similar) blocks. Returns (rows, meta) for whole file / first account."""
    accounts = parse_ofx_accounts(text)
    if not accounts:
        return [], {}
    # If multi-account, flatten rows for legacy single-account callers but keep first meta
    if len(accounts) == 1:
        a = accounts[0]
        return list(a["rows"]), dict(a["meta"])
    # Multi: merge rows (import_ofx single-target still works poorly; prefer multi API)
    all_rows: list[dict[str, Any]] = []
    for a in accounts:
        all_rows.extend(a["rows"])
    meta = dict(accounts[0]["meta"])
    meta["account_count"] = str(len(accounts))
    return all_rows, meta


def preview_ofx(file_obj: BinaryIO | TextIO | bytes | str | Path) -> dict[str, Any]:
    text = _read_text(file_obj)
    accounts = parse_ofx_accounts(text)
    total = sum(int(a.get("transactions_found") or 0) for a in accounts)
    first = accounts[0] if accounts else {}
    bal = first.get("ledger_balance")
    account_summaries = [
        {
            "acctid": a.get("acctid"),
            "bank_id": a.get("bank_id"),
            "org": a.get("org"),
            "accttype": a.get("accttype"),
            "kind": a.get("kind"),
            "external_key": a.get("external_key"),
            "transactions_found": a.get("transactions_found"),
            "ledger_balance": a.get("ledger_balance"),
            "ledger_balance_as_of": a.get("ledger_balance_as_of"),
            "sample": [
                {
                    "txn_date": r["txn_date"].isoformat(),
                    "payee": r["payee"],
                    "amount": str(r["amount"]),
                    "fitid": r.get("fitid") or "",
                }
                for r in (a.get("rows") or [])[:6]
            ],
        }
        for a in accounts
    ]
    multi = len(accounts) > 1
    return {
        "ok": total > 0 or any(a.get("ledger_balance") for a in accounts),
        "transactions_found": total,
        "account_count": len(accounts),
        "multi_account": multi,
        "account_hint": first.get("acctid"),
        "bank_id": first.get("bank_id"),
        "org": first.get("org"),
        "ledger_balance": bal,
        "ledger_balance_as_of": first.get("ledger_balance_as_of"),
        "accounts": account_summaries,
        "sample": account_summaries[0]["sample"] if account_summaries else [],
        "hint": (
            f"Multi-account OFX: {len(accounts)} accounts, {total} transactions — import maps each ACCTID "
            "(create/match HonestSpend accounts automatically)."
            if multi
            else (
                "OFX/QFX ready — import maps TRNAMT; LEDGERBAL sets institution balance for Reconcile."
                if bal
                else "OFX/QFX ready — import maps TRNAMT (bank sign: expenses usually negative)."
            )
            if total
            else (
                "Found ledger balance but no STMTTRN rows."
                if bal
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
        payee = r["payee"]
        if amount_sign == "invert":
            amt = -amt
        # Credit: same normalizer as CSV/PDF (payments/refunds positive, charges negative)
        if acct.kind == "credit" and amount_sign in ("bank", "invert"):
            from honestspend.services.import_amounts import normalize_credit_import_amount

            amt = normalize_credit_import_amount(amt, payee)
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
            from honestspend.services.account_balance import apply_amount_to_account

            apply_amount_to_account(acct, amt)
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
        from honestspend.services.reconcile import set_institution_balance

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

    # Learn ACCTID → account for future inbox auto-match
    if meta.get("acctid") and (
        result.transactions_created
        or result.skipped_existing
        or result.institution_balance_set
    ):
        key = ofx_external_account_key(meta["acctid"])
        if key and (not acct.external_id or str(acct.external_id).startswith("ofx:")):
            acct.external_id = key
            session.flush()

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
        institution_balance=result.ledger_balance if result.institution_balance_set else None,
        source="OFX/QFX",
    )
    return result


def _map_ofx_kind(kind: str | None) -> str:
    k = (kind or "checking").lower()
    if k in ("credit", "credit_card", "card"):
        return "credit"
    if k in ("savings", "saving", "mma", "moneymarket"):
        return "savings"
    return "checking"


def _find_or_create_ofx_account(
    session: Session,
    *,
    stmt: dict[str, Any],
    profile_id: int,
    auto_create: bool,
) -> Account | None:
    from honestspend.db import Account as Acc

    ext = stmt.get("external_key")
    if ext:
        existing = (
            session.query(Acc)
            .filter(Acc.external_id == ext)
            .order_by(Acc.id.asc())
            .first()
        )
        if existing:
            return existing
    # Match last-4 of ACCTID against nicknames / external
    acctid = (stmt.get("acctid") or "").strip()
    digits = re.sub(r"\D+", "", acctid)
    tail = digits[-4:] if len(digits) >= 4 else ""
    if tail:
        for a in session.query(Acc).all():
            nick = (a.nickname or "") + " " + (a.institution or "")
            if tail in nick or (a.external_id and tail in str(a.external_id)):
                if not a.external_id and ext:
                    a.external_id = ext
                    session.flush()
                return a
    if not auto_create:
        return None
    kind = _map_ofx_kind(stmt.get("kind"))
    org = (stmt.get("org") or stmt.get("bank_id") or "Bank").strip()
    label_tail = tail or (acctid[-6:] if acctid else "acct")
    nickname = f"{org} · {kind} · …{label_tail}"[:80]
    acct = Acc(
        profile_id=profile_id,
        kind=kind,
        nickname=nickname,
        institution=(org or None),
        current_balance=Decimal("0"),
        external_id=ext,
        is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
    )
    session.add(acct)
    session.flush()
    return acct


def import_ofx_multi(
    session: Session,
    *,
    file_obj: BinaryIO | TextIO | bytes | str | Path,
    filename: str = "download.ofx",
    auto_categorize: bool = True,
    amount_sign: str = "bank",
    apply_ledger_balance: bool = True,
    auto_create_accounts: bool = True,
    profile_id: int | None = None,
    account_map: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Import multi-account OFX/QFX (e.g. Canvas CU all-accounts download).

    account_map: optional {acctid or external_key -> account_id} overrides.
    """
    from honestspend.db import Profile

    text = _read_text(file_obj)
    statements = parse_ofx_accounts(text)
    if not statements:
        return {
            "ok": False,
            "account_count": 0,
            "accounts": [],
            "errors": ["No accounts or transactions found in OFX/QFX"],
        }

    if profile_id is None:
        prof = session.query(Profile).order_by(Profile.id.asc()).first()
        if not prof:
            return {
                "ok": False,
                "account_count": 0,
                "accounts": [],
                "errors": ["No profile/entity — create Personal or Business first"],
            }
        profile_id = int(prof.id)

    account_map = account_map or {}
    per_account: list[dict[str, Any]] = []
    total_created = 0
    total_found = 0
    errors: list[str] = []

    for stmt in statements:
        acctid = (stmt.get("acctid") or "").strip()
        ext = stmt.get("external_key") or ""
        target_id: int | None = None
        if acctid and acctid in account_map:
            target_id = int(account_map[acctid])
        elif ext and ext in account_map:
            target_id = int(account_map[ext])

        if target_id:
            acct = session.get(Account, target_id)
        else:
            acct = _find_or_create_ofx_account(
                session,
                stmt=stmt,
                profile_id=profile_id,
                auto_create=auto_create_accounts,
            )
        if not acct:
            errors.append(f"No HonestSpend account for bank ACCTID {acctid or '?'}")
            per_account.append(
                {
                    "acctid": acctid,
                    "matched": False,
                    "error": "no matching account",
                    "transactions_found": stmt.get("transactions_found"),
                }
            )
            continue

        # Import via existing single-account path using this statement block only
        res = import_ofx(
            session,
            account_id=int(acct.id),
            file_obj=stmt.get("block") or text,
            filename=filename,
            auto_categorize=auto_categorize,
            amount_sign=amount_sign,
            apply_ledger_balance=apply_ledger_balance,
        )
        total_created += res.transactions_created
        total_found += res.transactions_found
        if res.errors:
            errors.extend(res.errors[:3])
        per_account.append(
            {
                "acctid": acctid,
                "account_id": int(acct.id),
                "nickname": acct.nickname,
                "kind": acct.kind,
                "matched": True,
                "created_account": bool(ext and acct.external_id == ext and res.transactions_created >= 0),
                "transactions_found": res.transactions_found,
                "transactions_created": res.transactions_created,
                "skipped_existing": res.skipped_existing,
                "categorized": res.categorized,
                "ledger_balance": res.ledger_balance,
                "institution_balance_set": res.institution_balance_set,
                "schedules_advanced": res.schedules_advanced,
                "errors": res.errors[:5],
            }
        )

    return {
        "ok": total_created > 0 or total_found > 0 or any(a.get("matched") for a in per_account),
        "multi_account": len(statements) > 1,
        "account_count": len(statements),
        "transactions_found": total_found,
        "transactions_created": total_created,
        "accounts": per_account,
        "errors": errors[:12],
        "hint": (
            f"Imported {len(per_account)} bank account(s) from one file · "
            f"{total_created} new transactions"
        ),
    }
