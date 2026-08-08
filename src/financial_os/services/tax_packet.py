"""Year-end Tax Packet: group ledger by IRS form lines for TurboTax/CPA."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from financial_os.db import Category, Profile, Transaction

ZERO = Decimal("0")


@dataclass
class TaxLineBucket:
    tax_form: str
    tax_line: str
    display_name: str
    deductibility: str
    partial_rule: str | None
    gross: Decimal = ZERO
    deductible: Decimal = ZERO
    count: int = 0


def _apply_partial(amount: Decimal, rule: str | None) -> Decimal:
    """Expense amounts are negative in ledger; work with absolute spend then sign."""
    spend = -amount if amount < 0 else amount
    if rule == "meals_50":
        return spend * Decimal("0.5")
    if rule == "medical_agi_75":
        # Full tracked; AGI floor applied at tax time
        return spend
    if rule == "salt_cap":
        return spend
    return spend


def build_tax_packet(
    session: Session,
    *,
    profile_id: int,
    year: int,
) -> dict:
    profile = session.get(Profile, profile_id)
    if not profile:
        raise ValueError("Profile not found")

    start = date(year, 1, 1)
    end = date(year, 12, 31)
    txns = (
        session.query(Transaction)
        .filter(
            Transaction.profile_id == profile_id,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
            Transaction.is_transfer.is_(False),
        )
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )

    cats = {c.id: c for c in session.query(Category).all()}
    buckets: dict[tuple[str, str], TaxLineBucket] = {}
    uncategorized: list[dict] = []
    income_rows: list[dict] = []
    expense_rows: list[dict] = []
    owner_rows: list[dict] = []

    for t in txns:
        cat = cats.get(t.category_id) if t.category_id else None
        row = {
            "date": t.txn_date.isoformat(),
            "amount": str(t.amount),
            "payee": t.payee or "",
            "memo": t.memo or "",
            "category": cat.display_name if cat else "Uncategorized",
            "tax_form": cat.tax_form if cat else "none",
            "tax_line": cat.tax_line if cat and cat.tax_line else "",
            "deductibility": cat.deductibility if cat else "none",
            "code": cat.code if cat else "SYS_UNCATEGORIZED",
        }

        if not cat or cat.tax_form in ("none", "transfer") or not cat.tax_line:
            if not cat or cat.code.startswith("SYS_UNCATEGORIZED") or cat.tax_form == "none":
                uncategorized.append(row)
            if cat and cat.tax_form == "equity":
                owner_rows.append(row)
            # still record budget-only personal for transaction dump
            expense_rows.append(row) if t.amount < 0 else income_rows.append(row)
            continue

        if cat.tax_form == "equity":
            owner_rows.append(row)
            continue

        key = (cat.tax_form, str(cat.tax_line))
        if key not in buckets:
            buckets[key] = TaxLineBucket(
                tax_form=cat.tax_form,
                tax_line=str(cat.tax_line),
                display_name=cat.display_name,
                deductibility=cat.deductibility,
                partial_rule=cat.partial_rule,
            )
        b = buckets[key]
        b.count += 1
        b.gross += Decimal(t.amount)

        if t.amount < 0 and cat.deductibility in ("full", "partial", "capital", "special"):
            b.deductible += _apply_partial(Decimal(t.amount), cat.partial_rule)
        elif t.amount > 0:
            # income lines
            b.deductible += Decimal(t.amount)

        if t.amount >= 0:
            income_rows.append(row)
        else:
            expense_rows.append(row)

    by_line = sorted(buckets.values(), key=lambda x: (x.tax_form, x.tax_line))

    import json

    allocation = None
    raw_alloc = getattr(profile, "state_allocation_json", None)
    if raw_alloc:
        try:
            allocation = json.loads(raw_alloc)
        except Exception:
            allocation = raw_alloc

    readiness = tax_packet_readiness(session, profile_id=profile_id, year=year, packet_counts={
        "transactions": len(txns),
        "tax_lines": len(by_line),
        "uncategorized": len(uncategorized),
    })

    return {
        "profile": {
            "id": profile.id,
            "slug": profile.slug,
            "display_name": profile.display_name,
            "tax_form_primary": profile.tax_form_primary,
            "entity_type": profile.entity_type,
            "home_state": getattr(profile, "home_state", None),
            "multi_state": bool(getattr(profile, "multi_state", False)),
            "filing_notes": getattr(profile, "filing_notes", None),
            "state_allocation": allocation,
        },
        "year": year,
        "readiness": readiness,
        "disclaimer": (
            "Tax Packet for TurboTax/CPA entry. Not tax advice. "
            "Partial rules (e.g. meals 50%) applied where tagged; "
            "verify AGI floors, SALT caps, multi-state allocation, and S-corp K-1 separately stated items."
        ),
        "summary_by_tax_line": [
            {
                "tax_form": b.tax_form,
                "tax_line": b.tax_line,
                "category_sample": b.display_name,
                "txn_count": b.count,
                "gross_amount": str(b.gross),
                "tax_relevant_amount": str(b.deductible),
                "deductibility": b.deductibility,
                "partial_rule": b.partial_rule,
            }
            for b in by_line
        ],
        "transactions": expense_rows + income_rows,
        "uncategorized_or_nontax": uncategorized,
        "owner_activity": owner_rows,
        "counts": {
            "transactions": len(txns),
            "tax_lines": len(by_line),
            "uncategorized": len(uncategorized),
        },
    }


def tax_packet_readiness(
    session: Session,
    *,
    profile_id: int,
    year: int,
    packet_counts: dict | None = None,
    max_uncategorized: int = 25,
) -> dict:
    """Gate: is packet ready enough to hand a CPA?"""
    profile = session.get(Profile, profile_id)
    issues: list[str] = []
    warnings: list[str] = []

    if not profile:
        return {"ready": False, "issues": ["Profile not found"], "warnings": [], "score": 0}

    if packet_counts is None:
        # lightweight count only
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        uncat = (
            session.query(Transaction)
            .filter(
                Transaction.profile_id == profile_id,
                Transaction.txn_date >= start,
                Transaction.txn_date <= end,
                Transaction.is_transfer.is_(False),
                Transaction.category_id.is_(None),
            )
            .count()
        )
        total = (
            session.query(Transaction)
            .filter(
                Transaction.profile_id == profile_id,
                Transaction.txn_date >= start,
                Transaction.txn_date <= end,
                Transaction.is_transfer.is_(False),
            )
            .count()
        )
        packet_counts = {"transactions": total, "uncategorized": uncat, "tax_lines": 0}

    uncat = int(packet_counts.get("uncategorized") or 0)
    total = int(packet_counts.get("transactions") or 0)
    if total == 0:
        issues.append(f"No non-transfer transactions in {year}")
    if uncat > max_uncategorized:
        issues.append(f"{uncat} uncategorized/nontax rows (max recommended {max_uncategorized})")
    elif uncat > 0:
        warnings.append(f"{uncat} uncategorized/nontax rows — CPA will need notes")

    if not getattr(profile, "home_state", None):
        warnings.append("home_state not set on profile")
    if getattr(profile, "multi_state", False):
        raw = getattr(profile, "state_allocation_json", None)
        if not raw:
            warnings.append("multi_state is on but state_allocation_json is empty")
        else:
            try:
                import json

                alloc = json.loads(raw)
                if isinstance(alloc, list):
                    pct = sum(float(x.get("pct", 0)) for x in alloc if isinstance(x, dict))
                    if abs(pct - 100) > 1:
                        warnings.append(f"state allocation percentages sum to {pct}, not ~100")
            except Exception:
                warnings.append("state_allocation_json is not valid JSON")

    ready = len(issues) == 0
    score = 100 - 25 * len(issues) - 5 * len(warnings)
    score = max(0, min(100, score))
    return {
        "ready": ready,
        "score": score,
        "issues": issues,
        "warnings": warnings,
        "year": year,
        "profile_id": profile_id,
        "uncategorized": uncat,
        "transactions": total,
        "hint": "Ready means good enough for export — still not tax advice.",
    }


def packet_to_csv_files(packet: dict) -> dict[str, str]:
    """Return filename -> CSV text."""
    files: dict[str, str] = {}

    # expenses_by_tax_line
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "tax_form",
            "tax_line",
            "category_sample",
            "txn_count",
            "gross_amount",
            "tax_relevant_amount",
            "deductibility",
            "partial_rule",
        ]
    )
    for row in packet["summary_by_tax_line"]:
        w.writerow(
            [
                row["tax_form"],
                row["tax_line"],
                row["category_sample"],
                row["txn_count"],
                row["gross_amount"],
                row["tax_relevant_amount"],
                row["deductibility"],
                row["partial_rule"] or "",
            ]
        )
    files["expenses_by_tax_line.csv"] = buf.getvalue()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["date", "amount", "payee", "memo", "category", "tax_form", "tax_line", "deductibility", "code"]
    )
    for row in packet["transactions"]:
        w.writerow(
            [
                row["date"],
                row["amount"],
                row["payee"],
                row["memo"],
                row["category"],
                row["tax_form"],
                row["tax_line"],
                row["deductibility"],
                row["code"],
            ]
        )
    files["transactions.csv"] = buf.getvalue()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["date", "amount", "payee", "memo", "category", "tax_form", "tax_line", "code"]
    )
    for row in packet["owner_activity"]:
        w.writerow(
            [
                row["date"],
                row["amount"],
                row["payee"],
                row["memo"],
                row["category"],
                row["tax_form"],
                row["tax_line"],
                row["code"],
            ]
        )
    files["owner_activity.csv"] = buf.getvalue()

    slug = packet["profile"]["slug"]
    year = packet["year"]
    summary = [
        f"# Tax Packet — {packet['profile']['display_name']} ({year})",
        f"Primary form: {packet['profile']['tax_form_primary']}",
        f"Entity: {packet['profile']['entity_type']}",
        "",
        packet["disclaimer"],
        "",
        f"Transactions: {packet['counts']['transactions']}",
        f"Tax lines with activity: {packet['counts']['tax_lines']}",
        f"Uncategorized / nontax rows: {packet['counts']['uncategorized']}",
        "",
        "## By tax line",
    ]
    for row in packet["summary_by_tax_line"]:
        summary.append(
            f"- {row['tax_form']} line {row['tax_line']}: "
            f"gross {row['gross_amount']} / tax-relevant {row['tax_relevant_amount']} "
            f"({row['txn_count']} txns, {row['deductibility']}"
            f"{', ' + row['partial_rule'] if row['partial_rule'] else ''})"
        )
    files["year_summary.md"] = "\n".join(summary) + "\n"
    files["_meta_slug"] = slug
    files["_meta_year"] = str(year)
    return files


def write_tax_packet_dir(session: Session, profile_id: int, year: int, out_dir: Path) -> Path:
    packet = build_tax_packet(session, profile_id=profile_id, year=year)
    files = packet_to_csv_files(packet)
    slug = files.pop("_meta_slug")
    year_s = files.pop("_meta_year")
    target = out_dir / f"tax_packet_{slug}_{year_s}"
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (target / name).write_text(content, encoding="utf-8")
    return target
