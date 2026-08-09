"""Scan data_dir/inbox for bank CSVs and import them into accounts (freeware automation)."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

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
) -> Account | None:
    accounts = (
        session.query(Account)
        .filter(Account.archived_at.is_(None))
        .order_by(Account.id.asc())
        .all()
    )
    if not accounts:
        return None

    # OFX/QFX: prefer previously learned ACCTID binding
    if path.suffix.lower() in (".ofx", ".qfx"):
        try:
            from financial_os.services.bank_ofx import peek_ofx_meta

            meta = peek_ofx_meta(path)
            matched = match_account_by_ofx_acctid(session, meta.get("acctid"))
            if matched is not None:
                return matched
            # last-4 of ACCTID vs nickname/filename still via scoring below
            if meta.get("acctid"):
                last4 = re.sub(r"\D+", "", meta["acctid"])[-4:]
                if last4:
                    for a in accounts:
                        blob = f"{a.nickname or ''} {a.institution or ''} {a.external_id or ''}"
                        if last4 in re.sub(r"\D+", "", blob):
                            return a
        except Exception:
            pass

    stem = path.stem
    ranked = sorted(
        ((_score_account(a, stem), a.id, a) for a in accounts),
        key=lambda t: (-t[0], t[1]),
    )
    best_score, _, best = ranked[0]
    if best_score > 0:
        return best
    if default_account_id is not None:
        for a in accounts:
            if a.id == default_account_id:
                return a
    # fallback: first checking, else first account
    for a in accounts:
        if a.kind == "checking":
            return a
    return accounts[0]


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

    for meta in files:
        path = Path(meta["path"])
        match_mode = "filename"
        acct = resolve_account_for_file(session, path, default_account_id=default_account_id)
        if acct is None:
            results.append(
                {
                    "file": path.name,
                    "ok": False,
                    "error": "No accounts in books — run first-run setup first.",
                }
            )
            continue
        if path.suffix.lower() in (".ofx", ".qfx"):
            try:
                from financial_os.services.bank_ofx import peek_ofx_meta

                ofx_meta = peek_ofx_meta(path)
                linked = match_account_by_ofx_acctid(session, ofx_meta.get("acctid"))
                if linked is not None and linked.id == acct.id:
                    match_mode = "ofx_acctid"
            except Exception:
                pass
        entry: dict[str, Any] = {
            "file": path.name,
            "account_id": acct.id,
            "account_nickname": acct.nickname,
            "match_score": _score_account(acct, path.stem),
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

            entry["ok"] = not res.errors or res.transactions_created > 0
            entry["transactions_created"] = res.transactions_created
            entry["skipped_existing"] = res.skipped_existing
            entry["skipped_bad"] = res.skipped_bad
            entry["categorized"] = res.categorized
            entry["errors"] = res.errors[:5]
            if res.transactions_created or res.skipped_existing:
                any_ok = True
                total_created += res.transactions_created
                total_categorized += int(res.categorized or 0)
                profile_ids.add(acct.profile_id)
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

        # Prefer the first touched profile; books brief is per-entity
        pid = sorted(profile_ids)[0]
        next_steps = build_post_import_next_steps(
            session,
            profile_id=pid,
            created=total_created,
            categorized=total_categorized,
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
