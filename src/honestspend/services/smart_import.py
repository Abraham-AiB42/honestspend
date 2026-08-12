"""Smart multi-file bank import planning for average consumers.

Analyzes CSV / OFX / QFX / QIF / PDF downloads, scores personal vs business,
proposes entity + account mapping, then commits when the user confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, BinaryIO

from sqlalchemy.orm import Session

from honestspend.db import Account, Profile
from honestspend.services.bank_ofx import (
    import_ofx,
    ofx_external_account_key,
    parse_ofx_accounts,
    preview_ofx,
)
from honestspend.services.profiles import (
    apply_coa_for_profile,
    normalize_entity_type,
    unique_slug,
)

# --- Heuristic keyword banks -------------------------------------------------

_BUSINESS_NAME = re.compile(
    r"\b("
    r"llc|l\.l\.c|inc\.?|corp\.?|corporation|company|co\.|ltd|llp|pllc|"
    r"business|biz|dba|studio|agency|consulting|services|enterprises|"
    r"holdings|partners|group|shop|store|salon|clinic|dental|law|"
    r"payroll|vendor|invoice|client\s*payment|quickbooks|square\s*inc|"
    r"stripe|paypal\s*business|merchant|ein\b"
    r")\b",
    re.I,
)
_PERSONAL_NAME = re.compile(
    r"\b("
    r"personal|joint|family|household|checking\s*-\s*personal|"
    r"everyday|consumer|rewards\s*visa|debit\s*card"
    r")\b",
    re.I,
)
_BUSINESS_PAYEE = re.compile(
    r"\b("
    r"adp|gusto|paychex|intuit|quickbooks|square\s*inc|stripe|"
    r"godaddy|aws|amazon\s*web|google\s*workspace|microsoft\s*365|"
    r"office\s*depot|staples|fedex|ups\s*store|wework|regus|"
    r"sba|franchise|wholesale|supplier|vendor|contractor|"
    r"irs\s*usatax|state\s*tax|sales\s*tax|workers\s*comp"
    r")\b",
    re.I,
)
_PERSONAL_PAYEE = re.compile(
    r"\b("
    r"netflix|spotify|disney\s*\+|hulu|doordash|uber\s*eats|lyft|"
    r"starbucks|whole\s*foods|trader\s*joe|walmart|target|"
    r"mortgage|rent\s*payment|hoa|pediatric|daycare|school|"
    r"pharmacy|cvs|walgreens|costco|sams\s*club"
    r")\b",
    re.I,
)


@dataclass
class ScoreBreakdown:
    business: float = 0.0
    personal: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add(self, side: str, pts: float, reason: str) -> None:
        if side == "business":
            self.business += pts
        else:
            self.personal += pts
        self.reasons.append(reason)

    @property
    def entity_type(self) -> str:
        if self.business > self.personal + 0.15:
            return "business"
        if self.personal > self.business + 0.15:
            return "personal"
        # slight lean
        return "business" if self.business > self.personal else "personal"

    @property
    def confidence(self) -> float:
        total = self.business + self.personal
        if total <= 0:
            return 0.35
        winner = max(self.business, self.personal)
        return min(0.95, 0.4 + (winner / max(total, 0.01)) * 0.5)


def _score_text_blob(blob: str, score: ScoreBreakdown, weight: float = 1.0) -> None:
    if not blob:
        return
    if _BUSINESS_NAME.search(blob):
        score.add("business", 1.2 * weight, f"Name/org looks business: {blob[:40]}")
    if _PERSONAL_NAME.search(blob):
        score.add("personal", 1.1 * weight, f"Name looks personal: {blob[:40]}")
    biz_hits = len(_BUSINESS_PAYEE.findall(blob))
    per_hits = len(_PERSONAL_PAYEE.findall(blob))
    if biz_hits:
        score.add("business", min(2.0, 0.35 * biz_hits) * weight, f"{biz_hits} business-like payees")
    if per_hits:
        score.add("personal", min(2.0, 0.35 * per_hits) * weight, f"{per_hits} personal-like payees")


def _score_filename(name: str, score: ScoreBreakdown) -> None:
    base = (name or "").lower()
    _score_text_blob(base, score, weight=1.4)
    if re.search(r"\b(chk|checking|share|savings|visa|mc|amex|card)\b", base):
        score.add("personal", 0.2, "Filename account-type hints")
    if re.search(r"\b(biz|business|ops|operating|dba)\b", base):
        score.add("business", 0.9, "Filename business hint")


def _score_kind(kind: str, score: ScoreBreakdown) -> None:
    k = (kind or "").lower()
    if k == "credit":
        score.add("personal", 0.15, "Credit card (often personal; confirm)")
    if k in ("checking", "savings"):
        score.add("personal", 0.05, "Cash account")


def classify_account_source(
    *,
    filename: str,
    kind: str = "checking",
    org: str | None = None,
    accttype: str | None = None,
    acctid: str | None = None,
    sample_payees: list[str] | None = None,
) -> dict[str, Any]:
    score = ScoreBreakdown()
    _score_filename(filename, score)
    if org:
        _score_text_blob(org, score, weight=1.3)
    if accttype:
        _score_text_blob(accttype, score, weight=0.5)
    _score_kind(kind, score)
    payee_blob = " ".join(sample_payees or [])
    if payee_blob:
        _score_text_blob(payee_blob, score, weight=1.0)
    et = score.entity_type
    conf = score.confidence
    if not score.reasons:
        score.reasons.append("No strong signals — defaulting to Personal (you can change)")
        et = "personal"
        conf = 0.4
    return {
        "suggested_entity_type": et,
        "confidence": round(conf, 2),
        "reasons": score.reasons[:8],
        "scores": {"business": round(score.business, 2), "personal": round(score.personal, 2)},
    }


def _nickname(org: str | None, kind: str, acctid: str | None) -> str:
    digits = re.sub(r"\D+", "", acctid or "")
    tail = digits[-4:] if len(digits) >= 4 else (acctid or "")[-4:]
    base = (org or "Bank").strip()[:40]
    k = (kind or "checking").replace("_", " ")
    if tail:
        return f"{base} · {k} · …{tail}"[:80]
    return f"{base} · {k}"[:80]


# Boundary that treats _ - . as separators (filenames like chase_sapphire.csv)
_B = r"(?:^|[^A-Za-z0-9])"
_E = r"(?:[^A-Za-z0-9]|$)"

# Common bank / credit-union labels for filename + free-text sniffing
_BANK_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_B + r"(chase|jpmorgan|jp\s*morgan)" + _E, re.I), "Chase"),
    (re.compile(_B + r"(wells\s*fargo|wellsfargo|wf\.com)" + _E, re.I), "Wells Fargo"),
    (re.compile(_B + r"(bank\s*of\s*america|bofa|bof\s*a)" + _E, re.I), "Bank of America"),
    (re.compile(_B + r"(citibank|citicards|citi)" + _E, re.I), "Citi"),
    (re.compile(_B + r"(capital\s*one|capone|capitalone)" + _E, re.I), "Capital One"),
    (re.compile(_B + r"(american\s*express|amex)" + _E, re.I), "American Express"),
    (re.compile(_B + r"(discover\s*card|discover)" + _E, re.I), "Discover"),
    (re.compile(_B + r"(usaa)" + _E, re.I), "USAA"),
    (re.compile(_B + r"(us\s*bank|usbank)" + _E, re.I), "U.S. Bank"),
    (re.compile(_B + r"(pnc)" + _E, re.I), "PNC"),
    (re.compile(_B + r"(td\s*bank|tdbank)" + _E, re.I), "TD Bank"),
    (re.compile(_B + r"(truist|bb&t|suntrust)" + _E, re.I), "Truist"),
    (re.compile(_B + r"(navy\s*federal|nfcu)" + _E, re.I), "Navy Federal"),
    (re.compile(_B + r"(canvascu|canvas\s*cu|canvas)" + _E, re.I), "Canvas Credit Union"),
    (re.compile(_B + r"(ally)" + _E, re.I), "Ally"),
    (re.compile(_B + r"(sofi)" + _E, re.I), "SoFi"),
    (re.compile(_B + r"(schwab)" + _E, re.I), "Schwab"),
    (re.compile(_B + r"(fidelity)" + _E, re.I), "Fidelity"),
    (re.compile(_B + r"(venmo)" + _E, re.I), "Venmo"),
    (re.compile(_B + r"(paypal)" + _E, re.I), "PayPal"),
    (re.compile(_B + r"(apple\s*card)" + _E, re.I), "Apple Card"),
]


def guess_bank_label(*blobs: str | None) -> str | None:
    text = " ".join(b for b in blobs if b)
    if not text:
        return None
    for pat, label in _BANK_ALIASES:
        if pat.search(text):
            return label
    return None


def last4_from_id(acctid: str | None) -> str | None:
    digits = re.sub(r"\D+", "", acctid or "")
    if len(digits) >= 4:
        return digits[-4:]
    return None


def last4_from_text(*blobs: str | None) -> str | None:
    """Pick a plausible account last-4 from filename / statement text."""
    text = " ".join(b for b in blobs if b)
    if not text:
        return None
    # Prefer explicit masks: ****1234, …1234, x1234, ending in 1234
    for pat in (
        r"(?:\*{2,}|x{2,}|…|\.{2,}|ending\s*(?:in)?\s*)(\d{4})\b",
        r"(?:acct|account|card)\D{0,12}(\d{4})\b",
        r"[-_](\d{4})(?:\.[a-z]+)?$",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


def _kind_label(kind: str | None) -> str:
    k = (kind or "checking").lower()
    return {
        "checking": "Checking",
        "savings": "Savings",
        "credit": "Credit card",
        "cash": "Cash",
    }.get(k, k.replace("_", " ").title())


def _date_range_from_rows(rows: list[dict[str, Any]] | None) -> tuple[str | None, str | None]:
    dates = []
    for r in rows or []:
        d = r.get("txn_date")
        if d is None:
            continue
        if hasattr(d, "isoformat"):
            dates.append(d)
        else:
            try:
                from datetime import date as date_cls

                dates.append(date_cls.fromisoformat(str(d)[:10]))
            except Exception:
                pass
    if not dates:
        return None, None
    return min(dates).isoformat(), max(dates).isoformat()


def enrich_review_fields(
    acc: dict[str, Any],
    *,
    filename: str,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """Add idiot-proof review fields so users don't guess which file is which.

    Fills: bank_label, last4, human_title, review_lines, date_from/to, what_we_will_do.
    """
    kind = acc.get("kind") or "checking"
    org = (acc.get("org") or acc.get("bank_id") or "").strip() or None
    acctid = acc.get("acctid")
    bank = (
        guess_bank_label(org, filename, raw_text or "", acc.get("suggested_nickname") or "")
        or org
    )
    last4 = last4_from_id(str(acctid) if acctid else None) or last4_from_text(
        filename, raw_text or "", str(acctid or "")
    )
    # Prefer richer nickname when we learned bank / last4
    nick = (acc.get("suggested_nickname") or "").strip()
    if bank or last4:
        better = _nickname(bank or org or "Bank", kind, last4 or (str(acctid) if acctid else None))
        # Keep user-meaningful QIF account names when they look intentional
        if not nick or nick.lower() in ("bank · checking", "bank", "checking", filename.lower()):
            nick = better
        elif bank and bank.lower() not in nick.lower() and last4 and last4 not in nick:
            nick = better
    if not nick:
        stem = re.sub(r"\.[^.]+$", "", filename)
        nick = re.sub(r"[_\-]+", " ", stem).strip()[:50] or _nickname(bank, kind, last4)

    d0, d1 = acc.get("date_from"), acc.get("date_to")
    if not d0 and acc.get("rows"):
        d0, d1 = _date_range_from_rows(acc.get("rows"))
    # sample dates from sample payees structure sometimes not present

    n_txn = int(acc.get("transactions_found") or 0)
    bal = acc.get("ledger_balance")
    fmt = (acc.get("file_format") or "file").upper()
    kind_l = _kind_label(kind)

    title_bits = [bank or "Account", kind_l]
    if last4:
        title_bits.append(f"…{last4}")
    human_title = " · ".join(title_bits)

    lines: list[str] = []
    lines.append(f"File: {filename} ({fmt})")
    if bank:
        lines.append(f"Bank / institution: {bank}")
    if last4:
        lines.append(f"Account ending: …{last4}")
    elif acctid and len(str(acctid)) <= 12:
        lines.append(f"Account id hint: {acctid}")
    lines.append(f"Type: {kind_l}")
    if n_txn:
        lines.append(f"Transactions found: {n_txn}")
    if d0 and d1:
        lines.append(f"Date range: {d0} → {d1}")
    elif d0:
        lines.append(f"From: {d0}")
    if bal:
        lines.append(f"Statement / ledger balance: ${bal}")
    payees = [p for p in (acc.get("sample_payees") or []) if p]
    if payees:
        lines.append("Sample payees: " + ", ".join(str(p)[:28] for p in payees[:4]))
    # Confidence note
    conf = float(acc.get("confidence") or 0.5)
    et = acc.get("suggested_entity_type") or "personal"
    entity_word = "Personal" if et == "personal" else "Business"
    if conf >= 0.7:
        lines.append(f"Guess: {entity_word} books (high confidence)")
    else:
        lines.append(f"Guess: {entity_word} books — change if wrong")

    what = f'We will create account “{nick}” under {entity_word}'
    if acc.get("action") == "match" and acc.get("matched_nickname"):
        what = f'We will match existing “{acc.get("matched_nickname")}”'

    reasons = list(acc.get("reasons") or [])
    if bank and f"Bank label: {bank}" not in reasons:
        reasons.insert(0, f"Bank label: {bank}")
    if last4 and f"Last 4: …{last4}" not in reasons:
        reasons.insert(0 if not bank else 1, f"Last 4: …{last4}")

    acc = {
        **acc,
        "suggested_nickname": nick[:80],
        "bank_label": bank,
        "last4": last4,
        "human_title": human_title[:100],
        "review_lines": lines,
        "date_from": d0,
        "date_to": d1,
        "what_we_will_do": what,
        "reasons": reasons[:10],
        "institution": bank or org,
    }
    return acc


def _match_account(session: Session, *, external_key: str | None, acctid: str | None, nickname_hint: str) -> Account | None:
    if external_key:
        hit = session.query(Account).filter(Account.external_id == external_key).first()
        if hit:
            return hit
    digits = re.sub(r"\D+", "", acctid or "")
    tail = digits[-4:] if len(digits) >= 4 else ""
    if tail:
        for a in session.query(Account).all():
            blob = f"{a.nickname or ''} {a.institution or ''} {a.external_id or ''}"
            if tail in blob:
                return a
    # loose nickname
    hint = (nickname_hint or "").lower()
    if hint:
        for a in session.query(Account).all():
            if (a.nickname or "").lower() == hint:
                return a
    return None


def _match_profile(session: Session, entity_type: str, display_hint: str | None = None) -> Profile | None:
    et = normalize_entity_type(entity_type)
    q = session.query(Profile).filter(Profile.entity_type.in_([et, "individual"] if et == "personal" else [et]))
    profiles = q.order_by(Profile.id.asc()).all()
    if not profiles:
        # also accept legacy personal string
        profiles = (
            session.query(Profile)
            .filter(Profile.entity_type == et)
            .order_by(Profile.id.asc())
            .all()
        )
    if display_hint:
        h = display_hint.lower()
        for p in profiles:
            if h in (p.display_name or "").lower() or h in (p.slug or "").lower():
                return p
    return profiles[0] if profiles else None


def _analyze_ofx_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_ofx import _read_text

    text = _read_text(content)
    accounts = parse_ofx_accounts(text)
    out: list[dict[str, Any]] = []
    for a in accounts:
        payees = [str(r.get("payee") or "") for r in (a.get("rows") or [])[:40]]
        kind = a.get("kind") or "checking"
        org = a.get("org") or a.get("bank_id")
        cls = classify_account_source(
            filename=filename,
            kind=kind,
            org=org,
            accttype=a.get("accttype"),
            acctid=a.get("acctid"),
            sample_payees=payees,
        )
        nick = _nickname(org, kind, a.get("acctid"))
        d0, d1 = _date_range_from_rows(a.get("rows"))
        rec = {
            "source_key": a.get("external_key") or f"ofx:{(a.get('acctid') or nick)}",
            "file_format": "ofx",
            "acctid": a.get("acctid"),
            "external_key": a.get("external_key"),
            "kind": kind,
            "org": a.get("org") or org,
            "bank_id": a.get("bank_id"),
            "accttype": a.get("accttype"),
            "transactions_found": a.get("transactions_found"),
            "ledger_balance": a.get("ledger_balance"),
            "suggested_nickname": nick,
            "date_from": d0,
            "date_to": d1,
            "rows": a.get("rows"),  # stripped later in plan
            **cls,
            "sample_payees": payees[:6],
        }
        out.append(enrich_review_fields(rec, filename=filename, raw_text=text[:8000]))
    return out


def _analyze_csv_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_csv import preview_bank_csv

    raw_snip = ""
    try:
        raw_snip = content[:12000].decode("utf-8", errors="replace")
    except Exception:
        raw_snip = ""
    try:
        prev = preview_bank_csv(content)
    except Exception as e:
        rec = {
            "source_key": f"csv:{filename}",
            "file_format": "csv",
            "kind": "checking",
            "error": str(e)[:200],
            "suggested_nickname": filename[:60],
            "suggested_entity_type": "personal",
            "confidence": 0.3,
            "reasons": ["Could not parse CSV — still importable with mapping"],
            "transactions_found": 0,
        }
        return [enrich_review_fields(rec, filename=filename, raw_text=raw_snip)]
    sample = prev.get("sample") or []
    payees = []
    dates = []
    for row in sample[:40]:
        if isinstance(row, dict):
            payees.append(str(row.get("payee") or row.get("description") or ""))
            if row.get("txn_date"):
                dates.append(str(row.get("txn_date"))[:10])
        else:
            payees.append(str(row))
    kind = "credit" if re.search(r"\b(card|visa|amex|mc|credit)\b", filename, re.I) else "checking"
    if re.search(r"\b(sav|savings|mma)\b", filename, re.I):
        kind = "savings"
    bank = guess_bank_label(filename, raw_snip)
    cls = classify_account_source(
        filename=filename,
        kind=kind,
        org=bank,
        sample_payees=payees,
    )
    stem = re.sub(r"\.[^.]+$", "", filename)
    stem = re.sub(r"[_\-]+", " ", stem).strip()[:50]
    last4 = last4_from_text(filename, raw_snip)
    nick = _nickname(bank or "Bank", kind, last4) if (bank or last4) else (stem or _nickname(None, kind, None))
    d0 = min(dates) if dates else None
    d1 = max(dates) if dates else None
    rec = {
        "source_key": f"csv:{filename}",
        "file_format": "csv",
        "kind": kind,
        "org": bank,
        "acctid": last4,
        "suggested_nickname": nick,
        "transactions_found": prev.get("rows_scanned") or prev.get("transactions_found") or len(sample),
        "ledger_balance": prev.get("ending_balance"),
        "date_from": d0,
        "date_to": d1,
        **cls,
        "sample_payees": payees[:6],
        "preview_hint": prev.get("hint"),
    }
    return [enrich_review_fields(rec, filename=filename, raw_text=raw_snip)]


def _analyze_pdf_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    raw_snip = ""
    try:
        from honestspend.services.statement_pdf import extract_pdf_text, preview_statement_pdf

        prev = preview_statement_pdf(content)
        try:
            raw_snip, _ = extract_pdf_text(content)
            raw_snip = (raw_snip or "")[:12000]
        except Exception:
            raw_snip = ""
    except Exception as e:
        prev = {"error": str(e)[:200], "candidates": 0}
    kind = "credit" if re.search(r"\b(card|visa|amex|statement)\b", filename, re.I) else "checking"
    bank = guess_bank_label(filename, raw_snip)
    last4 = last4_from_text(filename, raw_snip)
    cls = classify_account_source(filename=filename, kind=kind, org=bank)
    stem = re.sub(r"\.[^.]+$", "", filename)
    nick = (
        _nickname(bank or "Bank", kind, last4)
        if (bank or last4)
        else (re.sub(r"[_\-]+", " ", stem).strip()[:50] or _nickname(None, kind, None))
    )
    rec = {
        "source_key": f"pdf:{filename}",
        "file_format": "pdf",
        "kind": kind,
        "org": bank,
        "acctid": last4,
        "suggested_nickname": nick,
        "transactions_found": prev.get("candidates") or 0,
        "ledger_balance": prev.get("ending_balance"),
        **cls,
        "preview_hint": prev.get("hint") or prev.get("error"),
    }
    return [enrich_review_fields(rec, filename=filename, raw_text=raw_snip)]


def _analyze_qif_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_qif import parse_qif_accounts, _read_text

    text = _read_text(content)
    accounts = parse_qif_accounts(text)
    out: list[dict[str, Any]] = []
    file_bank = guess_bank_label(filename, text[:4000])
    for a in accounts:
        payees = [str(r.get("payee") or "") for r in (a.get("rows") or [])[:40]]
        kind = a.get("kind") or "checking"
        name = (a.get("name") or "").strip()
        org = file_bank or name
        cls = classify_account_source(
            filename=filename,
            kind=kind,
            org=org,
            sample_payees=payees,
        )
        last4 = last4_from_text(name, filename)
        # Prefer bank + type + last4 when QIF account name is generic (!Type:Bank)
        if name and name.lower() not in ("bank", "ccard", "cash", "credit card", "oth a", "oth l"):
            nick = name[:50]
            if file_bank and file_bank.lower() not in name.lower():
                nick = f"{file_bank} · {name}"[:50]
        else:
            nick = _nickname(org or "Bank", kind, last4)
        d0, d1 = _date_range_from_rows(a.get("rows"))
        rec = {
            "source_key": a.get("external_key") or f"qif:{(name or nick)}",
            "file_format": "qif",
            "acctid": a.get("acctid") or name or last4,
            "external_key": a.get("external_key"),
            "kind": kind,
            "org": org,
            "transactions_found": a.get("transactions_found"),
            "suggested_nickname": nick,
            "date_from": d0,
            "date_to": d1,
            "rows": a.get("rows"),
            **cls,
            "sample_payees": payees[:6],
        }
        out.append(enrich_review_fields(rec, filename=filename, raw_text=text[:8000]))
    if not out:
        stem = re.sub(r"\.[^.]+$", "", filename)
        nick = re.sub(r"[_\-]+", " ", stem).strip()[:50] or "QIF account"
        cls = classify_account_source(filename=filename, kind="checking")
        rec = {
            "source_key": f"qif:{filename}",
            "file_format": "qif",
            "kind": "checking",
            "suggested_nickname": nick,
            "transactions_found": 0,
            **cls,
            "reasons": cls.get("reasons", []) + ["Empty or unreadable QIF"],
        }
        out.append(enrich_review_fields(rec, filename=filename, raw_text=text[:4000]))
    return out


def analyze_upload(filename: str, content: bytes) -> list[dict[str, Any]]:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("ofx", "qfx"):
        return _analyze_ofx_bytes(content, filename)
    if ext == "qif":
        return _analyze_qif_bytes(content, filename)
    if ext in ("csv", "txt"):
        return _analyze_csv_bytes(content, filename)
    if ext == "pdf":
        return _analyze_pdf_bytes(content, filename)
    if ext == "xlsx":
        return [
            {
                "source_key": f"xlsx:{filename}",
                "file_format": "xlsx",
                "kind": "checking",
                "suggested_nickname": filename[:50],
                "suggested_entity_type": "personal",
                "confidence": 0.35,
                "reasons": ["Excel workbook — maps via budget import path"],
                "transactions_found": 0,
            }
        ]
    return [
        {
            "source_key": f"file:{filename}",
            "file_format": ext or "unknown",
            "kind": "checking",
            "suggested_nickname": filename[:50],
            "suggested_entity_type": "personal",
            "confidence": 0.25,
            "reasons": [f"Unknown type .{ext} — may still import if supported"],
            "transactions_found": 0,
        }
    ]


def build_smart_plan(
    session: Session,
    files: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Build entity + account mapping plan for a batch of bank downloads."""
    existing_profiles = [
        {
            "id": p.id,
            "slug": p.slug,
            "display_name": p.display_name,
            "entity_type": p.entity_type,
        }
        for p in session.query(Profile).order_by(Profile.id.asc()).all()
    ]
    existing_accounts = [
        {
            "id": a.id,
            "nickname": a.nickname,
            "kind": a.kind,
            "profile_id": a.profile_id,
            "external_id": a.external_id,
            "institution": a.institution,
        }
        for a in session.query(Account).order_by(Account.id.asc()).all()
    ]

    sources: list[dict[str, Any]] = []
    entity_buckets: dict[str, dict[str, Any]] = {}

    for idx, (fname, content) in enumerate(files):
        accounts = analyze_upload(fname, content)
        file_entry: dict[str, Any] = {
            "file_index": idx,
            "filename": fname,
            "size_bytes": len(content),
            "accounts": [],
        }
        for acc in accounts:
            et = acc.get("suggested_entity_type") or "personal"
            # Stable entity key for this batch
            if et == "business":
                # Group businesses by org/filename stem when possible
                org = (acc.get("org") or "").strip()
                stem = re.sub(r"\.[^.]+$", "", fname)
                hint = org or stem
                ekey = "business"
                if _BUSINESS_NAME.search(hint) or org:
                    slug_bit = re.sub(r"[^a-z0-9]+", "-", hint.lower())[:24].strip("-") or "business"
                    ekey = f"business:{slug_bit}"
            else:
                ekey = "personal"

            if ekey not in entity_buckets:
                match = _match_profile(session, et, acc.get("org"))
                display = "Personal" if et == "personal" else (acc.get("org") or "Business")
                if et == "business" and ekey.startswith("business:"):
                    display = acc.get("org") or re.sub(r"\.[^.]+$", "", fname)[:40] or "Business"
                entity_buckets[ekey] = {
                    "key": ekey,
                    "entity_type": et,
                    "display_name": match.display_name if match else display,
                    "action": "use_existing" if match else "create",
                    "profile_id": match.id if match else None,
                    "confidence": acc.get("confidence", 0.5),
                }
            else:
                # raise confidence if stronger signal
                entity_buckets[ekey]["confidence"] = max(
                    float(entity_buckets[ekey].get("confidence") or 0),
                    float(acc.get("confidence") or 0),
                )

            ext = acc.get("external_key")
            matched = _match_account(
                session,
                external_key=ext,
                acctid=acc.get("acctid"),
                nickname_hint=acc.get("suggested_nickname") or "",
            )
            # If matched account's profile differs from suggested entity, prefer account's entity
            suggested_profile_id = entity_buckets[ekey].get("profile_id")
            if matched:
                suggested_profile_id = matched.profile_id
                for p in existing_profiles:
                    if p["id"] == matched.profile_id:
                        # keep entity key aligned
                        pass

            # Strip heavy rows payload before JSON to client
            clean = {k: v for k, v in acc.items() if k != "rows"}
            clean.update(
                {
                    "entity_key": ekey,
                    "action": "match" if matched else "create",
                    "account_id": matched.id if matched else None,
                    "matched_nickname": matched.nickname if matched else None,
                    "profile_id": suggested_profile_id,
                }
            )
            # Refresh what_we_will_do with final action
            nick = clean.get("suggested_nickname") or "account"
            et_word = "Personal" if ekey == "personal" or ekey.startswith("personal") else "Business"
            if clean.get("entity_key") and not str(clean["entity_key"]).startswith("business"):
                et_word = "Personal"
            if matched:
                clean["what_we_will_do"] = f'We will match existing “{matched.nickname}”'
            else:
                clean["what_we_will_do"] = f'We will create “{nick}” under {et_word}'
            file_entry["accounts"].append(clean)
        sources.append(file_entry)

    entities = list(entity_buckets.values())
    # Ensure at least personal exists in plan if any personal suggested
    if not any(e["entity_type"] == "personal" for e in entities):
        match = _match_profile(session, "personal")
        entities.insert(
            0,
            {
                "key": "personal",
                "entity_type": "personal",
                "display_name": match.display_name if match else "Personal",
                "action": "use_existing" if match else "create",
                "profile_id": match.id if match else None,
                "confidence": 0.9,
            },
        )

    n_acct = sum(len(s.get("accounts") or []) for s in sources)
    n_biz = sum(1 for e in entities if e.get("entity_type") == "business")
    n_with_bank = sum(
        1
        for s in sources
        for a in (s.get("accounts") or [])
        if a.get("bank_label") or a.get("last4")
    )
    labels = []
    for s in sources:
        for a in s.get("accounts") or []:
            t = a.get("human_title") or a.get("suggested_nickname")
            if t and t not in labels:
                labels.append(t)
    label_preview = ", ".join(labels[:4])
    if len(labels) > 4:
        label_preview += f" (+{len(labels) - 4} more)"

    return {
        "ok": True,
        "file_count": len(files),
        "account_source_count": n_acct,
        "entities": entities,
        "existing_profiles": existing_profiles,
        "existing_accounts": existing_accounts,
        "sources": sources,
        "ready_for_one_tap": True,
        "summary": (
            f"We reviewed {len(files)} file(s) and found {n_acct} account(s)"
            + (f" · {n_with_bank} with bank / last-4 labels" if n_with_bank else "")
            + (f". {label_preview}" if label_preview else "")
            + ". Confirm names below — or just import."
        ),
        "hint": (
            "You do not need to know which download is which. "
            "We read each file (bank name, account ending, balances, payees) and named them for you. "
            "Only change a row if something looks wrong — then tap Import everything."
        ),
    }


def _ensure_entities(session: Session, entities: list[dict[str, Any]]) -> dict[str, int]:
    """Return map entity_key -> profile_id, creating as needed."""
    key_to_id: dict[str, int] = {}
    for ent in entities:
        key = ent.get("key") or "personal"
        action = (ent.get("action") or "create").lower()
        pid = ent.get("profile_id")
        if action == "use_existing" and pid:
            key_to_id[key] = int(pid)
            continue
        if pid and action != "create":
            key_to_id[key] = int(pid)
            continue
        et = normalize_entity_type(ent.get("entity_type") or "personal")
        # Reuse existing of same type if create not forced and one exists
        if action != "create":
            existing = _match_profile(session, et, ent.get("display_name"))
            if existing:
                key_to_id[key] = int(existing.id)
                continue
        name = (ent.get("display_name") or ("Personal" if et == "personal" else "Business")).strip()
        slug = unique_slug(session, name if et == "business" else et)
        from honestspend.services.profiles import DEFAULT_TAX_FORM

        p = Profile(
            slug=slug,
            display_name=name[:128],
            entity_type=et,
            tax_form_primary=DEFAULT_TAX_FORM.get(et, "1040"),
        )
        session.add(p)
        session.flush()
        try:
            apply_coa_for_profile(session, p)
        except Exception:
            pass
        key_to_id[key] = int(p.id)
    return key_to_id


def commit_smart_plan(
    session: Session,
    *,
    plan: dict[str, Any],
    files: list[tuple[str, bytes]],
    auto_categorize: bool = True,
    amount_sign: str = "bank",
) -> dict[str, Any]:
    """Execute user-confirmed (or auto) plan against uploaded file bytes."""
    entities = plan.get("entities") or []
    sources = plan.get("sources") or []
    key_to_profile = _ensure_entities(session, entities)

    results: list[dict[str, Any]] = []
    total_created = 0

    # Index plan account decisions by (file_index, source_key)
    decisions: dict[tuple[int, str], dict[str, Any]] = {}
    for src in sources:
        fi = int(src.get("file_index") or 0)
        for acc in src.get("accounts") or []:
            sk = acc.get("source_key") or ""
            decisions[(fi, sk)] = acc

    for idx, (fname, content) in enumerate(files):
        ext = (fname.rsplit(".", 1)[-1] if "." in fname else "").lower()
        analyzed = analyze_upload(fname, content)

        if ext in ("ofx", "qfx"):
            # Prefer multi import with explicit account map built from decisions
            from honestspend.services.bank_ofx import import_ofx_multi, parse_ofx_accounts
            from honestspend.services.bank_ofx import _read_text

            text = _read_text(content)
            stmts = parse_ofx_accounts(text)
            # Ensure accounts exist per decision
            account_map: dict[str, int] = {}
            for stmt in stmts:
                sk = stmt.get("external_key") or f"ofx:{(stmt.get('acctid') or '')}"
                dec = decisions.get((idx, sk)) or {}
                # also try matching analyzed keys
                if not dec:
                    for a in analyzed:
                        if a.get("acctid") == stmt.get("acctid"):
                            dec = decisions.get((idx, a.get("source_key") or "")) or a
                            break
                ekey = dec.get("entity_key") or "personal"
                profile_id = int(dec.get("profile_id") or key_to_profile.get(ekey) or next(iter(key_to_profile.values())))
                action = (dec.get("action") or "create").lower()
                acct_id = dec.get("account_id")
                if action == "match" and acct_id:
                    account_map[stmt.get("acctid") or sk] = int(acct_id)
                    if sk:
                        account_map[sk] = int(acct_id)
                    continue
                # create
                existing = _match_account(
                    session,
                    external_key=stmt.get("external_key"),
                    acctid=stmt.get("acctid"),
                    nickname_hint=dec.get("suggested_nickname") or "",
                )
                if existing and action != "create":
                    account_map[stmt.get("acctid") or sk] = int(existing.id)
                    continue
                kind = dec.get("kind") or stmt.get("kind") or "checking"
                nick = (dec.get("suggested_nickname") or _nickname(stmt.get("org"), kind, stmt.get("acctid")))[:80]
                acct = Account(
                    profile_id=profile_id,
                    kind=kind if kind in ("checking", "savings", "credit", "cash") else "checking",
                    nickname=nick,
                    institution=(stmt.get("org") or None),
                    current_balance=Decimal("0"),
                    external_id=stmt.get("external_key"),
                    is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
                )
                session.add(acct)
                session.flush()
                account_map[stmt.get("acctid") or sk] = int(acct.id)
                if sk:
                    account_map[sk] = int(acct.id)

            multi_res = import_ofx_multi(
                session,
                file_obj=content,
                filename=fname,
                auto_categorize=auto_categorize,
                amount_sign=amount_sign,
                auto_create_accounts=False,
                account_map=account_map,
            )
            total_created += int(multi_res.get("transactions_created") or 0)
            results.append({"filename": fname, "format": "ofx", **multi_res})
            continue

        if ext == "qif":
            from honestspend.services.bank_qif import import_qif_multi, parse_qif_accounts, _read_text as _qif_read

            stmts = parse_qif_accounts(_qif_read(content))
            account_map: dict[str, int] = {}
            for stmt in stmts:
                sk = stmt.get("external_key") or f"qif:{(stmt.get('name') or '')}"
                dec = decisions.get((idx, sk)) or {}
                if not dec:
                    for a in analyzed:
                        if a.get("source_key") == sk or a.get("acctid") == stmt.get("name"):
                            dec = decisions.get((idx, a.get("source_key") or "")) or a
                            break
                ekey = dec.get("entity_key") or "personal"
                profile_id = int(
                    dec.get("profile_id")
                    or key_to_profile.get(ekey)
                    or next(iter(key_to_profile.values()))
                )
                action = (dec.get("action") or "create").lower()
                acct_id = dec.get("account_id")
                name = stmt.get("name") or sk
                if action == "match" and acct_id:
                    account_map[name] = int(acct_id)
                    account_map[sk] = int(acct_id)
                    continue
                existing = _match_account(
                    session,
                    external_key=stmt.get("external_key"),
                    acctid=None,
                    nickname_hint=dec.get("suggested_nickname") or name,
                )
                if existing and action != "create":
                    account_map[name] = int(existing.id)
                    account_map[sk] = int(existing.id)
                    continue
                kind = dec.get("kind") or stmt.get("kind") or "checking"
                nick = (dec.get("suggested_nickname") or name)[:80]
                acct = Account(
                    profile_id=profile_id,
                    kind=kind if kind in ("checking", "savings", "credit", "cash") else "checking",
                    nickname=nick,
                    current_balance=Decimal("0"),
                    external_id=stmt.get("external_key"),
                    is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
                )
                session.add(acct)
                session.flush()
                account_map[name] = int(acct.id)
                account_map[sk] = int(acct.id)

            multi_res = import_qif_multi(
                session,
                file_obj=content,
                filename=fname,
                auto_categorize=auto_categorize,
                amount_sign=amount_sign,
                auto_create_accounts=False,
                account_map=account_map,
            )
            total_created += int(multi_res.get("transactions_created") or 0)
            results.append({"filename": fname, "format": "qif", **multi_res})
            continue

        # CSV / PDF — single account source per file typically
        for acc in analyzed:
            sk = acc.get("source_key") or f"file:{fname}"
            dec = decisions.get((idx, sk)) or acc
            ekey = dec.get("entity_key") or "personal"
            profile_id = int(
                dec.get("profile_id")
                or key_to_profile.get(ekey)
                or next(iter(key_to_profile.values()))
            )
            action = (dec.get("action") or "create").lower()
            acct_id = dec.get("account_id")
            if action == "match" and acct_id:
                target_id = int(acct_id)
            else:
                existing = _match_account(
                    session,
                    external_key=None,
                    acctid=None,
                    nickname_hint=dec.get("suggested_nickname") or "",
                )
                if existing and action != "create":
                    target_id = int(existing.id)
                else:
                    kind = dec.get("kind") or "checking"
                    nick = (dec.get("suggested_nickname") or fname)[:80]
                    acct = Account(
                        profile_id=profile_id,
                        kind=kind if kind in ("checking", "savings", "credit", "cash") else "checking",
                        nickname=nick,
                        current_balance=Decimal("0"),
                        is_cash_for_ifpp=kind in ("checking", "savings", "cash"),
                    )
                    session.add(acct)
                    session.flush()
                    target_id = int(acct.id)

            if ext in ("csv", "txt"):
                from honestspend.services.bank_csv import import_bank_csv

                res = import_bank_csv(
                    session,
                    account_id=target_id,
                    file_obj=content,
                    filename=fname,
                    auto_categorize=auto_categorize,
                    amount_sign=amount_sign,
                )
                created = getattr(res, "transactions_created", 0) or 0
                total_created += created
                results.append(
                    {
                        "filename": fname,
                        "format": "csv",
                        "account_id": target_id,
                        "transactions_created": created,
                        "transactions_found": getattr(res, "rows_scanned", 0),
                        "skipped_existing": getattr(res, "skipped_existing", 0),
                        "errors": getattr(res, "errors", [])[:5],
                    }
                )
            elif ext == "pdf":
                try:
                    from honestspend.services.statement_pdf import import_statement_pdf

                    res = import_statement_pdf(
                        session,
                        account_id=target_id,
                        file_obj=content,
                        filename=fname,
                        auto_categorize=auto_categorize,
                        amount_sign=amount_sign,
                    )
                    created = getattr(res, "transactions_created", 0) or res.get("transactions_created", 0) if isinstance(res, dict) else 0
                    if not isinstance(created, int):
                        created = 0
                    total_created += created
                    results.append(
                        {
                            "filename": fname,
                            "format": "pdf",
                            "account_id": target_id,
                            "transactions_created": created,
                            "result": res if isinstance(res, dict) else str(type(res)),
                        }
                    )
                except Exception as e:
                    results.append({"filename": fname, "format": "pdf", "error": str(e)[:300]})
            elif ext == "xlsx":
                try:
                    import tempfile
                    from pathlib import Path as P

                    from honestspend.services.excel_import import import_budget_xlsx

                    prof = session.get(Profile, profile_id)
                    slug = prof.slug if prof else "personal"
                    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    try:
                        res = import_budget_xlsx(session, P(tmp_path), profile_slug=slug)
                        results.append(
                            {
                                "filename": fname,
                                "format": "xlsx",
                                "transactions_created": getattr(res, "transactions_created", 0),
                                "errors": getattr(res, "errors", [])[:5],
                            }
                        )
                    finally:
                        try:
                            P(tmp_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                except Exception as e:
                    results.append({"filename": fname, "format": "xlsx", "error": str(e)[:300]})
            else:
                results.append({"filename": fname, "format": ext, "error": "unsupported"})

    session.flush()
    total_skipped = 0
    for r in results:
        total_skipped += int(r.get("skipped_existing") or 0)
        if isinstance(r.get("accounts"), list):
            for a in r["accounts"]:
                if isinstance(a, dict):
                    total_skipped += int(a.get("skipped_existing") or 0)

    from honestspend.services.import_bootstrap import bootstrap_books_after_import
    from honestspend.services.import_dedupe import human_dup_summary

    bootstrap = bootstrap_books_after_import(
        session,
        profile_ids=list({int(v) for v in key_to_profile.values()}),
        lookback_days=730,
        auto_accept_recurring=True,
        auto_accept_discover=True,
        auto_categorize=True,
    )

    plain = human_dup_summary(
        created=total_created, skipped=total_skipped, files=len(files)
    )
    boot_msg = (bootstrap or {}).get("customer_message") or ""
    customer = (plain + " " + boot_msg).strip()
    return {
        "ok": True,
        "transactions_created": total_created,
        "duplicates_skipped": total_skipped,
        "results": results,
        "entities_resolved": key_to_profile,
        "bootstrap": bootstrap,
        "summary": f"{customer} Processed {len(files)} file(s).",
        "hint": (
            "We pulled balances, APR/promo/limits when statements include them, "
            "set up recurring bills and cards we could prove from history, "
            "categorized what we could, and skipped duplicates. "
            "Home for Safe to spend; Sort charges for anything left."
        ),
        "customer_message": customer,
    }
