"""Remember CSV import column maps / sign conventions per institution."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import ImportPreset


def list_presets(session: Session) -> list[dict[str, Any]]:
    rows = session.query(ImportPreset).order_by(ImportPreset.institution_key).all()
    return [_to_dict(r) for r in rows]


def get_preset(session: Session, institution_key: str) -> dict[str, Any] | None:
    key = _norm_key(institution_key)
    row = session.query(ImportPreset).filter(ImportPreset.institution_key == key).first()
    return _to_dict(row) if row else None


def upsert_preset(
    session: Session,
    *,
    institution_key: str,
    amount_sign: str = "bank",
    mapping_json: dict | str | None = None,
    notes: str | None = None,
    account_id: int | None = None,
) -> dict[str, Any]:
    key = _norm_key(institution_key)
    if not key:
        raise ValueError("institution_key required")
    row = session.query(ImportPreset).filter(ImportPreset.institution_key == key).first()
    if not row:
        row = ImportPreset(institution_key=key)
        session.add(row)
    row.amount_sign = amount_sign if amount_sign in ("bank", "invert") else "bank"
    if mapping_json is not None:
        if isinstance(mapping_json, str):
            row.mapping_json = mapping_json
        else:
            row.mapping_json = json.dumps(mapping_json)
    if notes is not None:
        row.notes = notes
    if account_id is not None:
        row.account_id = account_id
    session.flush()
    return _to_dict(row)


def _norm_key(s: str) -> str:
    return " ".join((s or "").strip().lower().split())[:128]


def _to_dict(row: ImportPreset) -> dict[str, Any]:
    mapping = None
    if row.mapping_json:
        try:
            mapping = json.loads(row.mapping_json)
        except Exception:
            mapping = row.mapping_json
    return {
        "id": row.id,
        "institution_key": row.institution_key,
        "amount_sign": row.amount_sign,
        "mapping": mapping,
        "notes": row.notes,
        "account_id": row.account_id,
    }
