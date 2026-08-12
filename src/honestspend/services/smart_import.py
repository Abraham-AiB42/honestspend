"""Smart multi-file bank import planning for average consumers.

Analyzes CSV / OFX / QFX / PDF downloads, scores personal vs business,
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
    text = content.decode("utf-8", errors="replace") if isinstance(content, (bytes, bytearray)) else str(content)
    # parse_ofx_accounts wants str
    from honestspend.services.bank_ofx import _read_text

    text = _read_text(content)
    accounts = parse_ofx_accounts(text)
    out: list[dict[str, Any]] = []
    for a in accounts:
        payees = [str(r.get("payee") or "") for r in (a.get("rows") or [])[:40]]
        kind = a.get("kind") or "checking"
        cls = classify_account_source(
            filename=filename,
            kind=kind,
            org=a.get("org") or a.get("bank_id"),
            accttype=a.get("accttype"),
            acctid=a.get("acctid"),
            sample_payees=payees,
        )
        nick = _nickname(a.get("org") or a.get("bank_id"), kind, a.get("acctid"))
        out.append(
            {
                "source_key": a.get("external_key") or f"ofx:{(a.get('acctid') or nick)}",
                "file_format": "ofx",
                "acctid": a.get("acctid"),
                "external_key": a.get("external_key"),
                "kind": kind,
                "org": a.get("org"),
                "bank_id": a.get("bank_id"),
                "accttype": a.get("accttype"),
                "transactions_found": a.get("transactions_found"),
                "ledger_balance": a.get("ledger_balance"),
                "suggested_nickname": nick,
                **cls,
                "sample_payees": payees[:6],
            }
        )
    return out


def _analyze_csv_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    from honestspend.services.bank_csv import preview_bank_csv

    try:
        prev = preview_bank_csv(content)
    except Exception as e:
        return [
            {
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
        ]
    sample = prev.get("sample") or []
    payees = []
    for row in sample[:40]:
        if isinstance(row, dict):
            payees.append(str(row.get("payee") or row.get("description") or ""))
        else:
            payees.append(str(row))
    # Guess kind from filename
    kind = "credit" if re.search(r"\b(card|visa|amex|mc|credit)\b", filename, re.I) else "checking"
    if re.search(r"\b(sav|savings|mma)\b", filename, re.I):
        kind = "savings"
    cls = classify_account_source(
        filename=filename,
        kind=kind,
        sample_payees=payees,
    )
    nick = _nickname(None, kind, None)
    # Prefer cleaner nickname from filename stem
    stem = re.sub(r"\.[^.]+$", "", filename)
    stem = re.sub(r"[_\-]+", " ", stem).strip()[:50]
    if stem:
        nick = stem
    return [
        {
            "source_key": f"csv:{filename}",
            "file_format": "csv",
            "kind": kind,
            "suggested_nickname": nick,
            "transactions_found": prev.get("rows_scanned") or prev.get("transactions_found") or len(sample),
            "ledger_balance": prev.get("ending_balance"),
            **cls,
            "sample_payees": payees[:6],
            "preview_hint": prev.get("hint"),
        }
    ]


def _analyze_pdf_bytes(content: bytes, filename: str) -> list[dict[str, Any]]:
    try:
        from honestspend.services.statement_pdf import preview_statement_pdf

        prev = preview_statement_pdf(content)
    except Exception as e:
        prev = {"error": str(e)[:200], "candidates": 0}
    kind = "credit" if re.search(r"\b(card|visa|amex|statement)\b", filename, re.I) else "checking"
    cls = classify_account_source(filename=filename, kind=kind)
    stem = re.sub(r"\.[^.]+$", "", filename)
    nick = re.sub(r"[_\-]+", " ", stem).strip()[:50] or _nickname(None, kind, None)
    return [
        {
            "source_key": f"pdf:{filename}",
            "file_format": "pdf",
            "kind": kind,
            "suggested_nickname": nick,
            "transactions_found": prev.get("candidates") or 0,
            "ledger_balance": prev.get("ending_balance"),
            **cls,
            "preview_hint": prev.get("hint") or prev.get("error"),
        }
    ]


def analyze_upload(filename: str, content: bytes) -> list[dict[str, Any]]:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    if ext in ("ofx", "qfx"):
        return _analyze_ofx_bytes(content, filename)
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

            file_entry["accounts"].append(
                {
                    **{k: v for k, v in acc.items() if k != "rows"},
                    "entity_key": ekey,
                    "action": "match" if matched else "create",
                    "account_id": matched.id if matched else None,
                    "matched_nickname": matched.nickname if matched else None,
                    "profile_id": suggested_profile_id,
                }
            )
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
    return {
        "ok": True,
        "file_count": len(files),
        "account_source_count": n_acct,
        "entities": entities,
        "existing_profiles": existing_profiles,
        "existing_accounts": existing_accounts,
        "sources": sources,
        "summary": (
            f"{len(files)} file(s) · {n_acct} account source(s) · "
            f"{n_biz} business entity suggestion(s). "
            "Review personal vs business, then Import."
        ),
        "hint": (
            "We guessed personal vs business from file names, bank labels, and payees. "
            "Fix anything wrong below — then one tap imports everything into clean books."
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
