"""Named what-if scenarios (dream H2-D)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import WhatIfScenario
from honestspend.services.ifpp_simulate import simulate_ifpp


def list_scenarios(session: Session, *, profile_id: int | None = None) -> dict[str, Any]:
    q = session.query(WhatIfScenario).order_by(WhatIfScenario.id.desc())
    if profile_id is not None:
        q = q.filter(
            (WhatIfScenario.profile_id == profile_id) | (WhatIfScenario.profile_id.is_(None))
        )
    items = []
    for s in q.limit(50).all():
        items.append(_to_dict(s))
    return {"count": len(items), "scenarios": items}


def create_scenario(
    session: Session,
    *,
    name: str,
    extra_outflows: list[dict[str, Any]] | None = None,
    profile_id: int | None = None,
    scope: str | None = "entity",
    notes: str | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    extras = extra_outflows or []
    row = WhatIfScenario(
        name=name[:128],
        profile_id=profile_id,
        scope=scope or "entity",
        extra_outflows_json=json.dumps(extras),
        notes=(notes or "")[:512] or None,
        created_at=datetime.now(),
    )
    session.add(row)
    session.flush()
    return _to_dict(row)


def delete_scenario(session: Session, scenario_id: int) -> dict[str, Any]:
    row = session.get(WhatIfScenario, scenario_id)
    if not row:
        raise ValueError("Scenario not found")
    session.delete(row)
    session.flush()
    return {"ok": True, "deleted": scenario_id}


def run_scenario(
    session: Session,
    scenario_id: int,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    row = session.get(WhatIfScenario, scenario_id)
    if not row:
        raise ValueError("Scenario not found")
    extras = json.loads(row.extra_outflows_json or "[]")
    sim = simulate_ifpp(
        session,
        extra_outflows=extras,
        as_of=as_of,
        profile_id=row.profile_id,
        scope=row.scope or "entity",
    )
    return {
        "scenario": _to_dict(row),
        "simulation": sim,
    }


def _to_dict(s: WhatIfScenario) -> dict[str, Any]:
    try:
        extras = json.loads(s.extra_outflows_json or "[]")
    except json.JSONDecodeError:
        extras = []
    return {
        "id": s.id,
        "name": s.name,
        "profile_id": s.profile_id,
        "scope": s.scope,
        "extra_outflows": extras,
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def quick_scenario_from_amount(
    session: Session,
    *,
    name: str,
    amount: Decimal,
    on_date: date | None = None,
    profile_id: int | None = None,
    scope: str = "entity",
) -> dict[str, Any]:
    """Save + run a simple one-shot purchase scenario."""
    on_date = on_date or date.today()
    extras = [
        {
            "amount": float(abs(amount)),
            "name": name or "What-if",
            "on_date": on_date.isoformat(),
        }
    ]
    created = create_scenario(
        session,
        name=name or f"Buy ${amount}",
        extra_outflows=extras,
        profile_id=profile_id,
        scope=scope,
    )
    sim = simulate_ifpp(
        session,
        extra_outflows=extras,
        profile_id=profile_id,
        scope=scope,
    )
    return {"scenario": created, "simulation": sim}
