"""Scan data_dir/inbox for bank CSVs and import them into accounts (freeware automation)."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from decimal import Decimal

from financial_os.config import settings
from financial_os.db import Account
from financial_os.services.bank_csv import import_bank_csv
from financial_os.services.import_reminders import mark_import_activity


def inbox_dir() -> Path:
    p = Path(settings.data_dir) / "inbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_dir() -> Path:
    p = Path(settings.data_dir) / "inbox" / "archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_inbox_layout() -> dict[str, str]:
    """Create inbox + README so users know where to drop files."""
    inbox = inbox_dir()
    arch = archive_dir()
    readme = inbox / "README.txt"
    if not readme.exists():
        readme.write_text(
            "HonestSpend inbox\n"
            "================\n"
            "Drop bank exports here:\n"
            "  .csv  .ofx  .qfx  (best)\n"
            "  .pdf  text statements (best-effort)\n"
            "  e.g. chase.ofx, Everyday-card.csv, Primary-checking.qfx\n"
            "Name files with account nicknames when you can.\n"
            "OFX/QFX: after the first import, ACCTID is remembered for auto-match.\n"
            "\n"
            "Run:  honestspend import-inbox\n"
            "Or:   Import → Import inbox now · tray → Import inbox now\n"
            "Or:   scheduled task HonestSpend-ImportInbox\n"
            "\n"
            "Processed files move to inbox/archive/.\n"
            "HonestSpend never stores bank passwords.\n",
            encoding="utf-8",
        )
    return {"inbox": str(inbox), "archive": str(arch), "readme": str(readme)}


def _score_account(acct: Account, stem: str) -> int:
    """Higher = better match of filename stem to account."""
    s = stem.lower()
    score = 0
    nick = (acct.nickname or "").lower()
    inst = (acct.institution or "").lower()
    kind = (acct.kind or "").lower()
    if nick and nick in s:
        score += 50
    # token overlap
    for tok in re.split(r"[^a-z0-9]+", nick):
        if len(tok) >= 3 and tok in s:
            score += 15
    for tok in re.split(r"[^a-z0-9]+", inst):
        if len(tok) >= 3 and tok in s:
            score += 10
    # last-4 of learned OFX ACCTID in filename
    ext = (acct.external_id or "").lower()
    if ext.startswith("ofx:"):
        digits = re.sub(r"\D+", "", ext[4:])
        if len(digits) >= 4 and digits[-4:] in re.sub(r"\D+", "", s):
            score += 40
    # kind keywords
    kind_words = {
        "checking": ("checking", "chk", "chequing"),
        "savings": ("savings", "save", "sav"),
        "credit": ("credit", "card", "amex", "visa", "mc", "mastercard", "discover"),
        "loan": ("loan", "mortgage", "auto"),
    }
    for k, words in kind_words.items():
        if kind == k and any(w in s for w in words):
            score += 8
    # common bank tokens in filename
    banks = {
        "chase": "chase",
        "amex": "american express",
        "americanexpress": "american express",
        "capitalone": "capital one",
        "citi": "citi",
        "wellsfargo": "wells",
        "wells": "wells",
        "bofa": "bank of america",
        "boa": "bank of america",
        "discover": "discover",
        "usbank": "u.s. bank",
        "fidelity": "fidelity",
    }
    for token, needle in banks.items():
        if token in s.replace(" ", "") or token in s:
            if needle in inst or needle in nick or token in nick.replace(" ", ""):
                score += 20
    return score


# Filename match must clear a floor and beat the runner-up (avoid "card" → first credit).
# Nickname-in-stem is +50; weak kind+token alone is ~23.
MIN_FILENAME_SCORE = 25
MIN_SCORE_MARGIN = 10


def match_account_by_ofx_acctid(session: Session, acctid: str | None) -> Account | None:
    """Exact match on learned Account.external_id = ofx:{ACCTID}."""
    from financial_os.services.bank_ofx import ofx_external_account_key

    key = ofx_external_account_key(acctid)
    if not key:
        return None
    return (
        session.query(Account)
        .filter(Account.archived_at.is_(None), Account.external_id == key)
        .order_by(Account.id.asc())
        .first()
    )


def resolve_account_for_file(
    session: Session,
    path: Path,
    *,
    default_account_id: int | None = None,
) -> tuple[Account | None, int, str]:
    """Match inbox file → account.

    Returns (account|None, score, match_mode).
    Does **not** fall back to first checking — wrong-account imports poison ACCTID.
    """
    accounts = (
        session.query(Account)
        .filter(Account.archived_at.is_(None))
        .order_by(Account.id.asc())
        .all()
    )
    if not accounts:
        return None, 0, "none"

    # OFX/QFX: prefer previously learned ACCTID binding
    if path.suffix.lower() in (".ofx", ".qfx"):
        try:
            from financial_os.services.bank_ofx import peek_ofx_meta

            meta = peek_ofx_meta(path)
            matched = match_account_by_ofx_acctid(session, meta.get("acctid"))
            if matched is not None:
                return matched, 100, "ofx_acctid"
            # last-4 only when exactly one account matches (avoid wrong-card poison)
            if meta.get("acctid"):
                last4 = re.sub(r"\D+", "", meta["acctid"])[-4:]
                if last4:
                    hits = [
                        a
                        for a in accounts
                        if last4
                        in re.sub(
                            r"\D+",
                            "",
                            f"{a.nickname or ''}{a.institution or ''}{a.external_id or ''}",
                        )
                    ]
                    if len(hits) == 1:
                        return hits[0], 40, "ofx_last4"
        except Exception:
            pass

    stem = path.stem
    ranked = sorted(
        ((_score_account(a, stem), a.id, a) for a in accounts),
        key=lambda t: (-t[0], t[1]),
    )
    best_score, _, best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0
    if best_score >= MIN_FILENAME_SCORE:
        # Require clear win unless nickname-tier score (≥50)
        if second_score > 0 and best_score < 50 and (best_score - second_score) < MIN_SCORE_MARGIN:
            return None, best_score, "ambiguous"
        return best, best_score, "filename"
    if best_score > 0:
        # Weak kind-only match — refuse unless explicit default
        if default_account_id is not None:
            for a in accounts:
                if a.id == default_account_id:
                    return a, best_score, "default"
        return None, best_score, "weak"
    if default_account_id is not None:
        for a in accounts:
            if a.id == default_account_id:
                return a, 0, "default"
    return None, 0, "none"


def list_inbox_files() -> list[dict[str, Any]]:
    inbox = inbox_dir()
    out: list[dict[str, Any]] = []
    for p in sorted(inbox.iterdir()):
        if not p.is_file():
            continue
        if p.name.upper() == "README.TXT":
            continue
        if p.suffix.lower() not in (".csv", ".txt", ".pdf", ".ofx", ".qfx"):
            continue
        out.append(
            {
                "name": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "kind": p.suffix.lower().lstrip("."),
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return out


def process_inbox(
    session: Session,
    *,
    default_account_id: int | None = None,
    auto_categorize: bool = True,
    amount_sign: str = "bank",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import all CSV/OFX/PDF files in inbox; move successes to archive."""
    layout = ensure_inbox_layout()
    files = list_inbox_files()
    results: list[dict[str, Any]] = []
    total_created = 0
    total_categorized = 0
    any_ok = False
    profile_ids: set[int] = set()
    match_modes: list[str] = []
    # account_id -> (abs_drift, drift, books, inst) for set_books aggregation
    drift_by_acct: dict[int, tuple[Decimal, Decimal, str, str]] = {}
    last_success_account_id: int | None = None

    for meta in files:
        path = Path(meta["path"])
        acct, match_score, match_mode = resolve_account_for_file(
            session, path, default_account_id=default_account_id
        )
        if acct is None:
            results.append(
                {
                    "file": path.name,
                    "ok": False,
                    "match_mode": match_mode,
                    "match_score": match_score,
                    "error": (
                        "No accounts in books — run first-run setup first."
                        if not session.query(Account).filter(Account.archived_at.is_(None)).count()
                        else (
                            "Ambiguous account match — name file more specifically "
                            "(e.g. Primary-checking.csv) or opt in to Target account."
                            if match_mode == "ambiguous"
                            else (
                                "Weak filename match — name file with nickname "
                                "or opt in to Target account for unmatched files."
                                if match_mode == "weak"
                                else "Could not match account — name file with nickname "
                                "(e.g. Primary-checking.csv) or opt in to Target account."
                            )
                        )
                    ),
                }
            )
            continue
        entry: dict[str, Any] = {
            "file": path.name,
            "account_id": acct.id,
            "account_nickname": acct.nickname,
            "match_score": match_score,
            "match_mode": match_mode,
        }
        match_modes.append(match_mode)
        if dry_run:
            entry["ok"] = True
            entry["dry_run"] = True
            results.append(entry)
            continue
        try:
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                from financial_os.services.statement_pdf import import_statement_pdf

                with path.open("rb") as fh:
                    res = import_statement_pdf(
                        session,
                        account_id=acct.id,
                        file_obj=fh,
                        filename=path.name,
                        auto_categorize=auto_categorize,
                        amount_sign=amount_sign,
                    )
                entry["format"] = "pdf"
                entry["pages"] = getattr(res, "pages", None)
                entry["rows_scanned"] = res.lines_scanned
            elif suffix in (".ofx", ".qfx"):
                from financial_os.services.bank_ofx import import_ofx

                with path.open("rb") as fh:
                    res = import_ofx(
                        session,
                        account_id=acct.id,
                        file_obj=fh,
                        filename=path.name,
                        auto_categorize=auto_categorize,
                        amount_sign=amount_sign,
                    )
                entry["format"] = suffix.lstrip(".")
                entry["rows_scanned"] = res.transactions_found
                entry["account_hint"] = res.account_hint
                if res.ledger_balance:
                    entry["ledger_balance"] = res.ledger_balance
                    entry["drift"] = res.drift
            else:
                with path.open("rb") as fh:
                    res = import_bank_csv(
                        session,
                        account_id=acct.id,
                        file_obj=fh,
                        filename=path.name,
                        auto_categorize=auto_categorize,
                        amount_sign=amount_sign,
                    )
                entry["format"] = "csv"
                entry["rows_scanned"] = res.rows_scanned
                if getattr(res, "ending_balance", None):
                    entry["ledger_balance"] = res.ending_balance
                    entry["drift"] = res.drift

            inst_set = bool(getattr(res, "institution_balance_set", False))
            money_in = bool(
                res.transactions_created or res.skipped_existing or inst_set
            )
            # ok only for real money-in success (not empty error-free no-ops)
            entry["ok"] = money_in
            entry["transactions_created"] = res.transactions_created
            entry["skipped_existing"] = res.skipped_existing
            entry["skipped_bad"] = res.skipped_bad
            entry["categorized"] = res.categorized
            entry["institution_balance_set"] = inst_set
            entry["errors"] = res.errors[:5]
            entry["next_steps"] = list(getattr(res, "next_steps", None) or [])
            # Money-in success: new/skipped txns OR bank bal set (bal-only PDF/OFX)
            if money_in:
                any_ok = True
                total_created += res.transactions_created
                total_categorized += int(res.categorized or 0)
                profile_ids.add(acct.profile_id)
                last_success_account_id = acct.id
                # Track worst drift for set_books aggregation
                raw_drift = getattr(res, "drift", None) or entry.get("drift")
                books_b = getattr(res, "books_balance", None)
                inst_b = (
                    getattr(res, "ledger_balance", None)
                    or getattr(res, "ending_balance", None)
                    or entry.get("ledger_balance")
                )
                if raw_drift is not None and inst_b is not None:
                    try:
                        dval = Decimal(str(raw_drift))
                        if abs(dval) >= Decimal("0.01"):
                            drift_by_acct[acct.id] = (
                                abs(dval),
                                dval,
                                str(books_b or ""),
                                str(inst_b),
                            )
                    except Exception:
                        pass
                dest = archive_dir() / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
                shutil.move(str(path), str(dest))
                entry["archived_to"] = str(dest)
            elif res.errors:
                entry["ok"] = False
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
        results.append(entry)

    if any_ok and not dry_run:
        try:
            mark_import_activity(session)
        except Exception:
            pass
        session.flush()

    next_steps: list[dict[str, str]] = []
    if any_ok and not dry_run and profile_ids:
        from financial_os.services.import_brief import build_post_import_next_steps

        pid = sorted(profile_ids)[0]
        account_id = None
        drift: Decimal | None = None
        books_bal: str | None = None
        inst_bal: str | None = None
        if drift_by_acct:
            # Worst absolute drift first
            account_id, pack = max(drift_by_acct.items(), key=lambda kv: kv[1][0])
            _, drift, books_bal, inst_bal = pack
        elif last_success_account_id is not None:
            # Bal-less success: still surface enter_ending_bal for that account
            account_id = last_success_account_id
        total_skipped = sum(int(r.get("skipped_existing") or 0) for r in results)
        next_steps = build_post_import_next_steps(
            session,
            profile_id=pid,
            account_id=account_id,
            created=total_created,
            categorized=total_categorized,
            skipped_existing=total_skipped,
            drift=drift,
            books_balance=books_bal,
            institution_balance=inst_bal,
            source="inbox",
        )

    ofx_linked = sum(1 for m in match_modes if m == "ofx_acctid")
    return {
        "inbox": layout["inbox"],
        "archive": layout["archive"],
        "files_seen": len(files),
        "transactions_created": total_created,
        "categorized": total_categorized,
        "results": results,
        "next_steps": next_steps,
        "ofx_acctid_matches": ofx_linked,
        "hint": (
            "OFX ACCTID is remembered after first import; name files with nicknames for CSV/PDF."
            if files
            else "Inbox is empty — drop bank CSV/OFX exports here."
        ),
    }
