"""Post-import bootstrap — pull every signal we can so books feel complete fast.

After a dump of history/statements:
- Apply balances / credit limit / APR / promo / min payment from file text
- Auto-create high-confidence cards, loans, bills from patterns
- Auto-schedule stable recurring charges
- Auto-categorize obvious spend

Conservative thresholds so we don't invent junk; customer can always edit.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Account, Profile

ZERO = Decimal("0")


def _d(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        s = str(v).replace(",", "").replace("$", "").strip()
        if s.endswith("%"):
            s = s[:-1].strip()
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _pct_to_apr(v: Decimal) -> Decimal:
    """22.99 or 0.2299 → store as decimal rate (0.2299)."""
    if v > 1:
        return (v / Decimal("100")).quantize(Decimal("0.0001"))
    return v.quantize(Decimal("0.0001"))


_MONTH_NAME = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_loose_date(raw: str) -> date | None:
    raw = (raw or "").strip().replace(",", "")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2})\s+(\d{4})", raw)
    if m:
        mon = _MONTH_NAME.get(m.group(1).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(2)))
            except ValueError:
                return None
    return None


def _labeled_money(text: str, *labels: str) -> Decimal | None:
    for lab in labels:
        m = re.search(
            rf"{lab}\s*[:.]?\s*\$?\s*([\d,]+\.\d{{2}})",
            text,
            re.I,
        )
        if m:
            v = _d(m.group(1))
            if v is not None:
                return v
    return None


def extract_statement_enrichment(text: str) -> dict[str, Any]:
    """Pull credit/statement fields from OFX, PDF text, or CSV headers/footers."""
    if not text:
        return {}
    t = text.replace("\xa0", " ")
    out: dict[str, Any] = {}

    nb = _labeled_money(t, r"new\s+balance", r"total\s+balance", r"standard\s+balance")
    if nb is not None:
        out["new_balance"] = nb
    isb = _labeled_money(t, r"interest\s+saving\s+balance")
    if isb is not None:
        out["interest_saving_balance"] = isb
    std = _labeled_money(t, r"standard\s+balance")
    if std is not None:
        out["standard_balance"] = std

    # Credit limit
    for pat in (
        r"credit\s*limit[:\s]*\$?\s*([\d,]+\.?\d*)",
        r"total\s*credit\s*line[:\s]*\$?\s*([\d,]+\.?\d*)",
        r"<CREDITLIMIT>([^<\r\n]+)",
        r"limit[:\s]*\$?\s*([\d,]+\.\d{2})\b",
    ):
        m = re.search(pat, t, re.I)
        if m:
            v = _d(m.group(1))
            if v is not None and v > 100:
                out["credit_limit"] = v
                break

    # Available credit
    for pat in (
        r"available\s*credit[:\s]*\$?\s*([\d,]+\.?\d*)",
        r"<AVAILCREDIT>([^<\r\n]+)",
        r"credit\s*available[:\s]*\$?\s*([\d,]+\.?\d*)",
    ):
        m = re.search(pat, t, re.I)
        if m:
            v = _d(m.group(1))
            if v is not None and v >= 0:
                out["available_credit"] = v
                break

    # APR (purchase) — also "Purchases 16.74% (v)"
    for pat in (
        r"purchases?\s+([\d.]+)\s*%",
        r"(?:purchase\s+)?apr[:\s]*([\d.]+)\s*%",
        r"([\d.]+)\s*%\s*(?:purchase\s+)?(?:annual\s+percentage\s+rate|apr)",
        r"interest\s*rate[:\s]*([\d.]+)\s*%",
        r"variable\s*apr[:\s]*([\d.]+)\s*%",
    ):
        m = re.search(pat, t, re.I)
        if m:
            v = _d(m.group(1))
            if v is not None and 0 < v < 80:
                out["apr"] = _pct_to_apr(v)
                out["apr_display_pct"] = float(v if v > 1 else v * 100)
                break

    # Promo / intro APR
    promo_m = re.search(
        r"(?:intro|promotional|promo|0\s*%|zero\s*percent)[^\n%]{0,40}?([\d.]+)\s*%",
        t,
        re.I,
    )
    if re.search(r"\b0\s*%\b", t) and re.search(
        r"promo|intro|promotional|purchase\s*apr|deferred", t, re.I
    ):
        out["promo_apr"] = Decimal("0")
        out["promo_apr_display_pct"] = 0.0
    elif promo_m:
        v = _d(promo_m.group(1))
        if v is not None and v < 30:
            out["promo_apr"] = _pct_to_apr(v)
            out["promo_apr_display_pct"] = float(v if v > 1 else v * 100)

    # Promo end date
    for pat in (
        r"(?:promo|intro|0\s*%|promotional)[^\n]{0,60}?(?:until|through|ends?|expir\w*|thru)\s*"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+\s+\d{1,2},?\s+\d{4})",
        r"(?:until|through|ends?)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}).{0,20}?(?:promo|0\s*%)",
    ):
        m = re.search(pat, t, re.I)
        if m:
            raw = m.group(1).strip()
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%B %d, %Y", "%b %d, %Y"):
                try:
                    out["promo_end_date"] = datetime.strptime(raw.replace(",", ""), fmt.replace(",", "")).date()
                    break
                except ValueError:
                    continue
            if "promo_end_date" in out:
                break

    # Minimum payment
    for pat in (
        r"min(?:imum)?\s*(?:payment)?(?:\s*due)?[:\s]*\$?\s*([\d,]+\.?\d*)",
        r"payment\s*due[:\s]*\$?\s*([\d,]+\.?\d*)",
        r"<MINPAYMENT(?:DUE)?>([^<\r\n]+)",
        r"amount\s*due[:\s]*\$?\s*([\d,]+\.?\d*)",
    ):
        m = re.search(pat, t, re.I)
        if m:
            v = _d(m.group(1))
            if v is not None and 0 < v < 100000:
                out["min_payment"] = v
                break

    # Payment due day
    m = re.search(
        r"(?:payment\s*)?due\s*(?:date)?[:\s]*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",
        t,
        re.I,
    )
    if m:
        try:
            day = int(m.group(2) if int(m.group(1)) > 12 else m.group(1))
            # Prefer day-of-month from MM/DD
            mm, dd = int(m.group(1)), int(m.group(2))
            due_day = dd if mm <= 12 else mm
            if 1 <= due_day <= 31:
                out["payment_due_day"] = due_day
        except Exception:
            pass
    m2 = re.search(r"due\s*on\s*the\s*(\d{1,2})(?:st|nd|rd|th)?", t, re.I)
    if m2 and "payment_due_day" not in out:
        d = int(m2.group(1))
        if 1 <= d <= 31:
            out["payment_due_day"] = d

    # Month-name due: "Payment Due Date Aug 17, 2026" / "Payment Due Date: August 17, 2026"
    if "payment_due_day" not in out or "payment_due_date" not in out:
        md = re.search(
            r"(?:payment\s*)?due\s*(?:date)?[:\s]*"
            r"([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
            t,
            re.I,
        )
        if md:
            parsed = _parse_loose_date(md.group(1))
            if parsed:
                out["payment_due_date"] = parsed
                out["payment_due_day"] = parsed.day

    # Opening/Closing Date 06/19/26 - 07/18/26  (Chase)
    oc = re.search(
        r"opening/?closing\s+date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*[-–]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        t,
        re.I,
    )
    if oc:
        start_d = _parse_loose_date(oc.group(1))
        end_d = _parse_loose_date(oc.group(2))
        if start_d:
            out["statement_period_start"] = start_d
        if end_d:
            out["statement_close_date"] = end_d
            out["statement_close_day"] = end_d.day

    # Closing / statement day — require "date" so we don't hit "close of each billing"
    if "statement_close_date" not in out:
        m = re.search(
            r"(?:statement\s+closing\s+date|closing\s+date|statement\s+date)[:\s]*"
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            t,
            re.I,
        )
        if m:
            parsed = _parse_loose_date(m.group(1))
            if parsed:
                out["statement_close_date"] = parsed
                out["statement_close_day"] = parsed.day
        else:
            m2 = re.search(
                r"(?:statement\s+closing\s+date|closing\s+date)[:\s]*(\d{1,2})[/-](\d{1,2})",
                t,
                re.I,
            )
            if m2:
                try:
                    mm, dd = int(m2.group(1)), int(m2.group(2))
                    close_day = dd if mm <= 12 else mm
                    if 1 <= close_day <= 31:
                        out["statement_close_day"] = close_day
                except Exception:
                    pass

    # Statement period 07/09/2026 to 08/07/2026 → close = period end
    per = re.search(
        r"statement\s+period[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+to\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        t,
        re.I,
    )
    if per:
        start_d = _parse_loose_date(per.group(1))
        end_d = _parse_loose_date(per.group(2))
        if start_d:
            out["statement_period_start"] = start_d
        if end_d:
            out["statement_close_date"] = end_d
            out["statement_close_day"] = end_d.day

    # Full due date MM/DD/YYYY already captured as day — also keep date
    if "payment_due_date" not in out:
        mdn = re.search(
            r"(?:payment\s*)?due\s*(?:date)?[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            t,
            re.I,
        )
        if mdn:
            parsed = _parse_loose_date(mdn.group(1))
            if parsed:
                out["payment_due_date"] = parsed
                out.setdefault("payment_due_day", parsed.day)

    # Cash APY (credit-union statements)
    apy_m = re.search(
        r"(?:annual\s+percentage\s+yield|apy)(?:\s+earned)?[:\s]*([\d.]+)\s*%",
        t,
        re.I,
    )
    if apy_m:
        v = _d(apy_m.group(1))
        if v is not None and 0 < v < 20:
            out["apy"] = _pct_to_apr(v)
            out["apy_display_pct"] = float(v)

    # Rewards hint: "5% back at Amazon.com"
    rw = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*back\s+(?:at|on)\s+([A-Za-z0-9 .]{3,24})",
        t,
        re.I,
    )
    if rw:
        try:
            pct = float(rw.group(1))
            cat_raw = rw.group(2).lower()
            if "amazon" in cat_raw:
                cat = "amazon"
            else:
                cat = re.sub(r"[^a-z0-9]+", "", cat_raw)[:16] or "general"
            if 0 < pct <= 20:
                out["rewards_hint"] = {cat: pct}
        except ValueError:
            pass

    return out


def apply_enrichment_to_account(
    session: Session,
    account_id: int,
    enrich: dict[str, Any],
    *,
    only_if_empty: bool = True,
) -> dict[str, Any]:
    """Write extracted APR/limit/promo/min onto the account row."""
    acct = session.get(Account, account_id)
    if not acct or not enrich:
        return {"applied": [], "account_id": account_id}
    applied: list[str] = []

    def set_field(field: str, value: Any, label: str) -> None:
        nonlocal applied
        cur = getattr(acct, field, None)
        if only_if_empty and cur not in (None, "", ZERO):
            return
        setattr(acct, field, value)
        applied.append(label)

    if enrich.get("credit_limit") is not None and acct.kind in ("credit", "loan"):
        set_field("credit_limit", enrich["credit_limit"], f"credit limit ${enrich['credit_limit']}")
    if enrich.get("available_credit") is not None and acct.kind == "credit":
        set_field("available_credit", enrich["available_credit"], f"available credit ${enrich['available_credit']}")
    if enrich.get("apr") is not None and acct.kind in ("credit", "loan"):
        set_field("apr", enrich["apr"], f"APR {float(enrich.get('apr_display_pct') or enrich['apr'] * 100):.2f}%")
    if enrich.get("promo_apr") is not None and acct.kind == "credit":
        set_field("promo_apr", enrich["promo_apr"], f"promo APR {enrich.get('promo_apr_display_pct', 0)}%")
    if enrich.get("promo_end_date") is not None and acct.kind == "credit":
        set_field("promo_end_date", enrich["promo_end_date"], f"promo ends {enrich['promo_end_date']}")
    if enrich.get("min_payment") is not None and acct.kind in ("credit", "loan"):
        set_field("min_payment", enrich["min_payment"], f"min payment ${enrich['min_payment']}")
    if enrich.get("payment_due_day") is not None:
        set_field("payment_due_day", int(enrich["payment_due_day"]), f"due day {enrich['payment_due_day']}")
    if enrich.get("statement_close_day") is not None:
        set_field(
            "statement_close_day",
            int(enrich["statement_close_day"]),
            f"close day {enrich['statement_close_day']}",
        )
    if enrich.get("apy") is not None and acct.kind in ("checking", "savings", "cash"):
        set_field("apy", enrich["apy"], f"APY {enrich.get('apy_display_pct', 0)}%")
    if enrich.get("new_balance") is not None:
        # Always refresh institution_balance from this statement
        try:
            from honestspend.services.reconcile import set_institution_balance

            set_institution_balance(session, account_id, enrich["new_balance"], mark_reconciled=False)
            applied.append(f"statement balance ${enrich['new_balance']}")
        except Exception:
            pass
    if enrich.get("interest_saving_balance") is not None and acct.kind == "credit":
        applied.append(
            f"interest-saving balance ${enrich['interest_saving_balance']} (pay this to keep purchase grace)"
        )
    if enrich.get("rewards_hint") and not (acct.rewards_rates_json or "").strip():
        import json

        try:
            acct.rewards_rates_json = json.dumps(enrich["rewards_hint"])
            applied.append("rewards rates from statement")
        except Exception:
            pass

    if applied:
        session.flush()
    return {"applied": applied, "account_id": account_id, "nickname": acct.nickname}


def enrich_account_from_text(
    session: Session,
    account_id: int,
    text: str,
    *,
    only_if_empty: bool = True,
) -> dict[str, Any]:
    return apply_enrichment_to_account(
        session,
        account_id,
        extract_statement_enrichment(text),
        only_if_empty=only_if_empty,
    )


def _installment_terms_from_account(session: Session, account_id: int) -> list[dict[str, Any]]:
    """Apple-style MONTHLY INSTALLMENTS (n of m) activity rows → purchase_plan terms."""
    from honestspend.db import Transaction
    from honestspend.services.promo_statement_parse import (
        extract_installment_terms_from_payee_amounts,
    )

    rows = (
        session.query(Transaction.payee, Transaction.amount)
        .filter(
            Transaction.account_id == int(account_id),
            Transaction.status != "void",
        )
        .all()
    )
    return extract_installment_terms_from_payee_amounts([(p or "", a) for p, a in rows])


def _existing_installment_terms(session: Session, account_id: int) -> list[dict[str, Any]]:
    """Open Monthly installments · N mo lines already on the card (survive re-apply)."""
    from honestspend.db import PromoInstallmentLine
    from honestspend.services.promo_statement_parse import monthly_installment_plan_name

    out: list[dict[str, Any]] = []
    lines = (
        session.query(PromoInstallmentLine)
        .filter(
            PromoInstallmentLine.account_id == int(account_id),
            PromoInstallmentLine.kind == "purchase_plan",
        )
        .all()
    )
    for ln in lines:
        name = (ln.name or "").strip()
        if not name.lower().startswith("monthly installments"):
            continue
        monthly = _d(ln.monthly_payment) or ZERO
        if monthly <= ZERO:
            continue
        prin = _d(ln.principal_remaining) or ZERO
        months_left = int((prin / monthly).quantize(Decimal("1"))) if monthly else 0
        if months_left < 1:
            months_left = 1
        total = None
        m = re.search(r"(\d+)\s*mo\b", name, re.I)
        if m:
            try:
                total = int(m.group(1))
            except ValueError:
                total = None
        out.append(
            {
                "kind": "purchase_plan",
                "name": name or monthly_installment_plan_name(total or months_left),
                "principal_remaining": prin,
                "monthly_payment": monthly,
                "months_left": months_left,
                "total_payments": total,
                "source_kind": "monthly_installment",
            }
        )
    return out


def apply_statement_promos(
    session: Session,
    account_id: int,
    text: str,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """extract_promo_terms + Apple N-of-M activity → upsert_promo_term(source='statement').

    Then recompute_card_payment_schedule. Returns
    {created, updated, conflicts, lines, promos_found, promo_conflicts}.
    """
    from honestspend.services.promo_statement_parse import extract_promo_terms
    from honestspend.services.promo_upsert import upsert_promo_term

    as_of = as_of or date.today()
    created = 0
    updated = 0
    conflicts: list[Any] = []
    lines: list[Any] = []

    terms = extract_promo_terms(text or "")
    txn_terms = _installment_terms_from_account(session, account_id)
    existing_terms = _existing_installment_terms(session, account_id)
    if (
        txn_terms
        or existing_terms
        or any(t.get("source_kind") == "monthly_installment" for t in terms)
    ):
        from honestspend.services.promo_statement_parse import merge_installment_terms

        text_install = [t for t in terms if t.get("source_kind") == "monthly_installment"]
        other = [t for t in terms if t.get("source_kind") != "monthly_installment"]
        terms = other + merge_installment_terms(text_install, txn_terms, existing_terms)
    if not any(t.get("kind") in ("purchase_plan", "card_intro") for t in terms):
        from honestspend.services.promo_statement_parse import extract_interest_saving_balance

        isb = extract_interest_saving_balance(text or "")
        nb = extract_statement_enrichment(text or "").get("new_balance")
        if isb is not None and nb is not None and nb > isb + Decimal("1"):
            terms = list(terms) + [
                {
                    "kind": "card_intro",
                    "name": "Promotional / plan balances (from ISB)",
                    "principal_remaining": (nb - isb).quantize(Decimal("0.01")),
                    "monthly_payment": Decimal("0.00"),
                    "apr": Decimal("0"),
                }
            ]
    for term in terms:
        kind = (term.get("kind") or "purchase_plan").strip() or "purchase_plan"
        name = (term.get("name") or "").strip() or (
            "Intro 0%" if kind == "card_intro" else "Promo plan"
        )
        prin = term.get("principal_remaining")
        monthly = term.get("monthly_payment")
        if kind == "bnpl":
            continue
        if prin is None:
            if kind != "offer":
                continue
            prin = Decimal("0")
        if monthly is None:
            monthly = Decimal("0")
        end_date = term.get("end_date")
        start = as_of
        # months_left → approximate end when parse did not give a date
        if end_date is None and term.get("months_left") is not None:
            try:
                months = int(term["months_left"])
                y, m = as_of.year, as_of.month + months
                while m > 12:
                    y += 1
                    m -= 12
                end_date = date(y, min(m, 12), min(as_of.day, 28))
            except (TypeError, ValueError):
                end_date = None

        r = upsert_promo_term(
            session,
            account_id=account_id,
            kind=kind,
            name=name,
            principal_remaining=Decimal(str(prin)),
            monthly_payment=Decimal(str(monthly)),
            start_date=start,
            end_date=end_date,
            source="statement",
            offer_type=term.get("offer_type"),
            apr=term.get("apr"),
        )
        lines.append(r.get("line"))
        if r.get("created"):
            created += 1
        else:
            updated += 1
        if r.get("conflict"):
            conflicts.append(r["conflict"])

    # Always recompute after apply so next payment reflects open promo monthlies
    try:
        from honestspend.services.autopay import recompute_card_payment_schedule

        recompute_card_payment_schedule(session, account_id, as_of=as_of)
    except Exception:
        pass

    try:
        from honestspend.services.bnpl import apply_bnpl_for_account

        bnpl_out = apply_bnpl_for_account(session, account_id, text or "", as_of=as_of)
    except Exception:
        bnpl_out = {"created": 0, "updated": 0, "ended": 0}

    session.flush()
    return {
        "created": created,
        "updated": updated,
        "conflicts": conflicts,
        "lines": lines,
        "promos_found": created + updated,
        "promo_conflicts": len(conflicts),
        "bnpl_created": int(bnpl_out.get("created") or 0),
        "bnpl_updated": int(bnpl_out.get("updated") or 0),
    }


def bootstrap_books_after_import(
    session: Session,
    *,
    profile_ids: list[int] | None = None,
    lookback_days: int = 730,
    auto_accept_recurring: bool = True,
    auto_accept_discover: bool = True,
    auto_categorize: bool = True,
) -> dict[str, Any]:
    """After history is in: cards, bills, categories — high-confidence only."""
    from honestspend.services.categorizer import categorize_uncategorized
    from honestspend.services.recurring_detect import detect_recurring
    from honestspend.services.setup_discover import apply_discoveries, discover_liabilities
    from honestspend.services.setup_recurring_finalize import apply_recurring_choices

    if profile_ids is None:
        profile_ids = [int(p.id) for p in session.query(Profile).order_by(Profile.id.asc()).all()]

    bills_added = 0
    cards_added = 0
    loans_added = 0
    categorized = 0
    details: list[str] = []

    for pid in profile_ids:
        if auto_accept_discover:
            disc = discover_liabilities(
                session, profile_id=pid, lookback_days=lookback_days
            )
            accept: list[dict[str, Any]] = []
            for p in disc.get("proposals") or []:
                conf = float(p.get("confidence") or 0)
                occ = int(p.get("occurrences") or 0)
                ptype = (p.get("type") or "").lower()
                # Stricter for generic bills; looser for clear card/loan keywords
                if ptype in ("credit", "loan") and conf >= 0.68 and occ >= 2:
                    p = {**p, "selected": True}
                    accept.append(p)
                elif ptype == "bill" and conf >= 0.78 and occ >= 3:
                    p = {**p, "selected": True}
                    accept.append(p)
                elif ptype == "recurring_investment" and conf >= 0.8 and occ >= 2:
                    p = {**p, "selected": True}
                    accept.append(p)
            if accept:
                res = apply_discoveries(session, accepted=accept, profile_id=pid)
                for c in res.get("created") or []:
                    t = (c.get("type") or "").lower()
                    if t == "credit":
                        cards_added += 1
                    elif t == "loan":
                        loans_added += 1
                    elif t in ("bill", "recurring_investment"):
                        bills_added += 1
                    details.append(f"Added {t}: {c.get('name')}")

        if auto_accept_recurring:
            det = detect_recurring(
                session,
                profile_id=pid,
                lookback_days=lookback_days,
                min_occurrences=3,
                limit=25,
            )
            accept_r: list[dict[str, Any]] = []
            for s in det.get("suggestions") or []:
                occ = int(s.get("occurrences") or 0)
                stab = float(s.get("stability_0_1") or 0)
                score = float(s.get("score") or 0)
                if occ >= 3 and stab >= 0.55 and score >= 35:
                    accept_r.append({**s, "selected": True})
            if accept_r:
                rres = apply_recurring_choices(
                    session, accepted=accept_r, profile_id=pid
                )
                n = int(rres.get("accepted") or 0)
                bills_added += n
                if n:
                    details.append(f"Scheduled {n} recurring bill(s) for profile {pid}")

        if auto_categorize:
            try:
                for _ in range(12):
                    applied = categorize_uncategorized(
                        session,
                        profile_id=pid,
                        limit=500,
                        apply=True,
                        use_grok=False,
                        min_confidence=0.82,
                    )
                    n = sum(1 for x in applied if x.get("applied"))
                    categorized += n
                    if n == 0 or len(applied) < 500:
                        break
            except Exception:
                pass

    try:
        from honestspend.services.cycle_config import learn_card_funding_after_import

        learned = learn_card_funding_after_import(session)
        n_fund = int((learned or {}).get("filled") or 0)
        if n_fund:
            details.append(f"Linked {n_fund} card(s) to the cash account that pays them")
    except Exception:
        n_fund = 0

    # Recompute card payment schedules when possible
    try:
        from honestspend.services.autopay import recompute_all_card_payments

        recompute_all_card_payments(session)
    except Exception:
        try:
            from honestspend.services.autopay import recompute_card_payment_schedule

            for a in session.query(Account).filter(Account.kind == "credit").all():
                try:
                    recompute_card_payment_schedule(session, a.id)
                except Exception:
                    pass
        except Exception:
            pass

    session.flush()
    parts = []
    if cards_added:
        parts.append(f"{cards_added} card(s)")
    if loans_added:
        parts.append(f"{loans_added} loan(s)")
    if bills_added:
        parts.append(f"{bills_added} bill(s)/recurring")
    if categorized:
        parts.append(f"{categorized} categorized")
    if n_fund:
        parts.append(f"{n_fund} card pay-from")
    customer = (
        "Also set up from your history: " + ", ".join(parts) + "."
        if parts
        else "History loaded — open Sort charges if anything still needs a category."
    )
    return {
        "ok": True,
        "cards_added": cards_added,
        "loans_added": loans_added,
        "bills_added": bills_added,
        "categorized": categorized,
        "details": details[:40],
        "customer_message": customer,
        "lookback_days": lookback_days,
    }
