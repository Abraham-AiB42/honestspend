"""Fingerprint upsert for promo terms + D10 user-vs-incoming conflicts."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import PromoConflict, PromoInstallmentLine
from honestspend.services.promo_installments import (
    create_promo_line,
    sync_account_promo_balance_from_lines,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _d(v: Any) -> Decimal:
    if v is None:
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _q(v: Decimal | Any) -> Decimal:
    return _d(v).quantize(CENT)


def _norm_name(kind: str, name: str) -> str:
    n = " ".join((name or "").strip().lower().split())
    if n:
        return n
    if kind == "card_intro":
        return "__intro__"
    return "__isb__"


def promo_fingerprint(
    *,
    account_id: int | None,
    kind: str,
    name: str,
    monthly: Decimal | None,
    end_date: date | None,
) -> str:
    """account_id|kind|norm_name|monthly_or_end.

    purchase_plan uses monthly; card_intro uses end_date; name fallback __intro__ / __isb__.
    """
    kind_s = (kind or "purchase_plan").strip() or "purchase_plan"
    norm = _norm_name(kind_s, name)
    if kind_s == "card_intro":
        tail = end_date.isoformat() if end_date else ""
    else:
        tail = str(_q(monthly)) if monthly is not None else ""
    acct = "" if account_id is None else str(int(account_id))
    return f"{acct}|{kind_s}|{norm}|{tail}"


def _field_snapshot(
    *,
    principal_remaining: Decimal,
    monthly_payment: Decimal,
    end_date: date | None,
    apr: Decimal | None,
) -> dict[str, Any]:
    return {
        "principal_remaining": str(_q(principal_remaining)),
        "monthly_payment": str(_q(monthly_payment)),
        "end_date": end_date.isoformat() if end_date else None,
        "apr": str(apr) if apr is not None else None,
    }


def _line_snapshot(line: PromoInstallmentLine) -> dict[str, Any]:
    apr = getattr(line, "apr", None)
    return _field_snapshot(
        principal_remaining=_d(line.principal_remaining),
        monthly_payment=_d(line.monthly_payment),
        end_date=line.end_date,
        apr=_d(apr) if apr is not None else None,
    )


def _apr_equal(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return _d(a) == _d(b)


def _fields_differ(
    line: PromoInstallmentLine,
    *,
    principal_remaining: Decimal,
    monthly_payment: Decimal,
    end_date: date | None,
    apr: Decimal | None,
) -> list[str]:
    diffs: list[str] = []
    if _q(line.principal_remaining) != _q(principal_remaining):
        diffs.append("principal_remaining")
    if _q(line.monthly_payment) != _q(monthly_payment):
        diffs.append("monthly_payment")
    if line.end_date != end_date:
        diffs.append("end_date")
    line_apr = getattr(line, "apr", None)
    line_apr_d = _d(line_apr) if line_apr is not None else None
    if not _apr_equal(line_apr_d, apr):
        diffs.append("apr")
    return diffs


def _fields_empty(line: PromoInstallmentLine) -> bool:
    """True when remaining/monthly/end/apr are unset so silent fill is allowed."""
    rem = _d(line.principal_remaining)
    pay = _d(line.monthly_payment)
    apr = getattr(line, "apr", None)
    return rem == ZERO and pay == ZERO and line.end_date is None and apr is None


def _find_by_fingerprint(
    session: Session, fingerprint: str
) -> PromoInstallmentLine | None:
    return (
        session.query(PromoInstallmentLine)
        .filter(PromoInstallmentLine.fingerprint == fingerprint)
        .order_by(PromoInstallmentLine.id)
        .first()
    )


def _apply_values(
    line: PromoInstallmentLine,
    *,
    principal_remaining: Decimal,
    monthly_payment: Decimal,
    start_date: date,
    end_date: date | None,
    apr: Decimal | None,
    source: str,
    name: str | None = None,
    offer_type: str | None = None,
) -> None:
    rem = _q(principal_remaining)
    line.principal_remaining = rem
    line.monthly_payment = _q(monthly_payment)
    line.start_date = start_date
    line.end_date = end_date
    line.apr = _d(apr) if apr is not None else None
    line.source = (source or "user").strip() or "user"
    if name is not None:
        line.name = (name or "").strip() or line.name
    if offer_type is not None:
        line.offer_type = offer_type
    if rem <= ZERO:
        line.principal_remaining = ZERO
        line.active = False
        line.status = "inactive"
    else:
        line.active = True
        if (getattr(line, "status", None) or "") in ("", "inactive"):
            line.status = "open"


def _conflict_to_dict(row: PromoConflict) -> dict[str, Any]:
    return {
        "id": row.id,
        "line_id": row.line_id,
        "incoming_source": row.incoming_source,
        "field_diffs": json.loads(row.field_diffs_json or "[]"),
        "incoming_values": json.loads(row.incoming_values_json or "{}"),
        "user_values": json.loads(row.user_values_json or "{}"),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _insert_conflict(
    session: Session,
    line: PromoInstallmentLine,
    *,
    incoming_source: str,
    diffs: list[str],
    incoming_values: dict[str, Any],
    user_values: dict[str, Any],
) -> dict[str, Any]:
    row = PromoConflict(
        line_id=int(line.id),
        incoming_source=(incoming_source or "").strip() or "statement",
        field_diffs_json=json.dumps(diffs),
        incoming_values_json=json.dumps(incoming_values),
        user_values_json=json.dumps(user_values),
        resolved_at=None,
    )
    session.add(row)
    session.flush()
    return _conflict_to_dict(row)


def _silent_update_source(existing_source: str, incoming_source: str) -> str:
    """Among silent sources, statement wins over plaid."""
    if existing_source == "statement" or incoming_source == "statement":
        return "statement"
    return incoming_source


def upsert_promo_term(
    session: Session,
    *,
    account_id: int | None,
    kind: str,
    name: str,
    principal_remaining: Decimal,
    monthly_payment: Decimal,
    start_date: date,
    end_date: date | None,
    source: str,
    offer_type: str | None = None,
    apr: Decimal | None = None,
) -> dict:
    """Returns {line, created, conflict}.

    If matching fingerprint and existing.source=='user' and remaining/monthly/end/apr differ:
      insert PromoConflict, do not mutate line, conflict=dict.
    If existing.source in (statement, plaid) or fields empty: update line (statement may update plaid;
      plaid must NOT update statement — create conflict instead).
    If no match: create line with that source.
    """
    kind_s = (kind or "purchase_plan").strip() or "purchase_plan"
    source_s = (source or "user").strip() or "user"
    name_s = (name or "").strip() or (
        "Intro 0%" if kind_s == "card_intro" else "Promo plan"
    )
    prin = _q(principal_remaining)
    monthly = _q(monthly_payment)
    apr_d = _d(apr) if apr is not None else None

    fp = promo_fingerprint(
        account_id=account_id,
        kind=kind_s,
        name=name_s,
        monthly=monthly,
        end_date=end_date,
    )

    existing = _find_by_fingerprint(session, fp)
    if existing is None:
        line = create_promo_line(
            session,
            account_id,
            name=name_s,
            principal_remaining=prin,
            monthly_payment=monthly,
            start_date=start_date,
            end_date=end_date,
            source=source_s,
            kind=kind_s,
            offer_type=offer_type,
            fingerprint=fp,
            apr=apr_d,
            active=prin > ZERO,
            status="inactive" if prin <= ZERO else None,
        )
        return {"line": line, "created": True, "conflict": None}

    incoming_snap = _field_snapshot(
        principal_remaining=prin,
        monthly_payment=monthly,
        end_date=end_date,
        apr=apr_d,
    )
    user_snap = _line_snapshot(existing)
    diffs = _fields_differ(
        existing,
        principal_remaining=prin,
        monthly_payment=monthly,
        end_date=end_date,
        apr=apr_d,
    )
    existing_source = (existing.source or "user").strip() or "user"

    # Plaid must not silent-update a statement-sourced line when values differ.
    if source_s == "plaid" and existing_source == "statement" and diffs:
        conflict = _insert_conflict(
            session,
            existing,
            incoming_source=source_s,
            diffs=diffs,
            incoming_values=incoming_snap,
            user_values=user_snap,
        )
        return {"line": existing, "created": False, "conflict": conflict}

    # User-sourced lines never change silently when values differ (unless empty).
    if existing_source == "user" and diffs and not _fields_empty(existing):
        conflict = _insert_conflict(
            session,
            existing,
            incoming_source=source_s,
            diffs=diffs,
            incoming_values=incoming_snap,
            user_values=user_snap,
        )
        return {"line": existing, "created": False, "conflict": conflict}

    if not diffs:
        return {"line": existing, "created": False, "conflict": None}

    # Silent update path: statement/plaid (or import/isb), or fill empty fields.
    if existing_source == "user":
        new_source = "user"
    else:
        new_source = _silent_update_source(existing_source, source_s)

    _apply_values(
        existing,
        principal_remaining=prin,
        monthly_payment=monthly,
        start_date=start_date,
        end_date=end_date,
        apr=apr_d,
        source=new_source,
        name=name_s,
        offer_type=offer_type,
    )
    session.flush()
    if existing.account_id is not None:
        sync_account_promo_balance_from_lines(
            session, int(existing.account_id), as_of=start_date
        )
    return {"line": existing, "created": False, "conflict": None}


def resolve_promo_conflict(
    session: Session,
    conflict_id: int,
    *,
    action: str,
    edits: dict | None = None,
) -> PromoInstallmentLine:
    """keep_user | take_incoming | edit. Sets conflict.resolved_at.

    edit/take_incoming write source=user or incoming_source.
    """
    row = session.get(PromoConflict, conflict_id)
    if not row:
        raise ValueError(f"Promo conflict {conflict_id} not found")
    line = session.get(PromoInstallmentLine, row.line_id)
    if not line:
        raise ValueError(f"Promo line {row.line_id} not found")
    if row.resolved_at is not None:
        return line

    act = (action or "").strip().lower()
    if act == "keep_user":
        pass
    elif act == "take_incoming":
        incoming = json.loads(row.incoming_values_json or "{}")
        prin = _q(incoming.get("principal_remaining", line.principal_remaining))
        monthly = _q(incoming.get("monthly_payment", line.monthly_payment))
        end_raw = incoming.get("end_date")
        end = date.fromisoformat(end_raw) if end_raw else None
        apr_raw = incoming.get("apr")
        apr_d = _d(apr_raw) if apr_raw is not None else None
        _apply_values(
            line,
            principal_remaining=prin,
            monthly_payment=monthly,
            start_date=line.start_date,
            end_date=end,
            apr=apr_d,
            source=row.incoming_source or "statement",
        )
    elif act == "edit":
        e = edits or {}
        prin = (
            _q(e["principal_remaining"])
            if "principal_remaining" in e
            else _q(line.principal_remaining)
        )
        monthly = (
            _q(e["monthly_payment"])
            if "monthly_payment" in e
            else _q(line.monthly_payment)
        )
        if "end_date" in e:
            end_raw = e["end_date"]
            if isinstance(end_raw, str):
                end = date.fromisoformat(end_raw) if end_raw else None
            else:
                end = end_raw
        else:
            end = line.end_date
        if "apr" in e:
            apr_raw = e["apr"]
            apr_d = _d(apr_raw) if apr_raw is not None else None
        else:
            line_apr = getattr(line, "apr", None)
            apr_d = _d(line_apr) if line_apr is not None else None
        name = e.get("name")
        _apply_values(
            line,
            principal_remaining=prin,
            monthly_payment=monthly,
            start_date=line.start_date,
            end_date=end,
            apr=apr_d,
            source="user",
            name=name if name is not None else None,
        )
    else:
        raise ValueError(
            f"Unknown resolve action {action!r}; use keep_user | take_incoming | edit"
        )

    row.resolved_at = datetime.now(tz=None)
    session.flush()
    if line.account_id is not None:
        sync_account_promo_balance_from_lines(session, int(line.account_id))
    return line


def list_open_conflicts(
    session: Session, *, account_id: int | None = None
) -> list[dict]:
    """Unresolved promo conflicts, optionally filtered by account_id."""
    q = (
        session.query(PromoConflict)
        .filter(PromoConflict.resolved_at.is_(None))
        .order_by(PromoConflict.id)
    )
    if account_id is not None:
        q = q.join(
            PromoInstallmentLine, PromoInstallmentLine.id == PromoConflict.line_id
        ).filter(PromoInstallmentLine.account_id == account_id)
    return [_conflict_to_dict(row) for row in q.all()]
