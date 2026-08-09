"""CPA pack: tax packet CSV zip + rules export + optional cpa_viewer token."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from io import BytesIO
from typing import Any

from sqlalchemy.orm import Session

from financial_os.db import AppUser, Category, CategoryRule, Profile
from financial_os.services.permissions import Role, generate_api_token
from financial_os.services.tax_packet import build_tax_packet, packet_to_csv_files, tax_packet_readiness


def _rules_csv(session: Session) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "pattern",
            "match_type",
            "priority",
            "category_id",
            "category_name",
            "source",
            "active",
            "is_transfer",
        ]
    )
    rows = session.query(CategoryRule).order_by(CategoryRule.priority.desc()).all()
    for r in rows:
        cat = session.get(Category, r.category_id)
        w.writerow(
            [
                r.id,
                r.pattern,
                r.match_type,
                r.priority,
                r.category_id,
                cat.display_name if cat else "",
                r.source,
                r.active,
                r.is_transfer,
            ]
        )
    return buf.getvalue()


def _profile_notes_txt(profile: Profile) -> str:
    lines = [
        f"Entity: {profile.display_name} ({profile.slug})",
        f"Type: {profile.entity_type}",
        f"Primary form: {profile.tax_form_primary}",
        f"Home state: {getattr(profile, 'home_state', None) or '—'}",
        f"Multi-state: {bool(getattr(profile, 'multi_state', False))}",
        f"Filing notes: {getattr(profile, 'filing_notes', None) or '—'}",
        f"State allocation JSON: {getattr(profile, 'state_allocation_json', None) or '—'}",
        "",
        "Not tax advice. Verify S-corp reasonable compensation, K-1, multi-state, AGI floors.",
    ]
    return "\n".join(lines) + "\n"


def build_cpa_pack_zip(
    session: Session,
    *,
    profile_id: int,
    year: int | None = None,
    issue_token: bool = True,
    token_username: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    y = year or date.today().year
    profile = session.get(Profile, profile_id)
    if not profile:
        raise ValueError("Profile not found")

    packet = build_tax_packet(session, profile_id=profile_id, year=y)
    readiness = packet.get("readiness") or tax_packet_readiness(
        session, profile_id=profile_id, year=y
    )
    files = packet_to_csv_files(packet)
    # drop internal meta keys if present
    files = {k: v for k, v in files.items() if not k.startswith("_")}

    meta = {
        "product": "HonestSpend",
        "year": y,
        "profile_id": profile_id,
        "profile_slug": profile.slug,
        "readiness": readiness,
        "generated": date.today().isoformat(),
        "disclaimer": packet.get("disclaimer"),
    }

    token_info = None
    if issue_token:
        uname = token_username or f"cpa_{profile.slug}_{y}"
        existing = session.query(AppUser).filter(AppUser.username == uname).first()
        token = generate_api_token()
        if existing:
            existing.api_token = token
            existing.role = Role.cpa_viewer.value
            existing.active = True
            existing.display_name = existing.display_name or f"CPA · {profile.display_name}"
            user_id = existing.id
        else:
            row = AppUser(
                username=uname,
                display_name=f"CPA · {profile.display_name}",
                role=Role.cpa_viewer.value,
                active=True,
                api_token=token,
            )
            session.add(row)
            session.flush()
            user_id = row.id
        token_info = {
            "user_id": user_id,
            "username": uname,
            "role": Role.cpa_viewer.value,
            "api_token": token,
            "hint": "Send as X-API-Key. Shown once in pack README — store securely.",
        }
        meta["cpa_token_username"] = uname

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(f"tax/{name}", content)
        zf.writestr("rules/category_rules.csv", _rules_csv(session))
        zf.writestr("entity_notes.txt", _profile_notes_txt(profile))
        zf.writestr("readiness.json", json.dumps(readiness, indent=2))
        zf.writestr("META.json", json.dumps(meta, indent=2))
        readme = [
            "HonestSpend CPA Pack",
            f"Entity: {profile.display_name}",
            f"Year: {y}",
            f"Ready: {readiness.get('ready')}",
            "",
            "Contents:",
            "  tax/           — tax packet CSVs",
            "  rules/         — category rules export",
            "  entity_notes.txt",
            "  readiness.json",
            "  META.json",
            "",
            packet.get("disclaimer") or "",
            "",
        ]
        if token_info:
            readme.extend(
                [
                    "API access (read + tax export):",
                    f"  username: {token_info['username']}",
                    f"  X-API-Key: {token_info['api_token']}",
                    "  Store this token securely; rotate via Users page if needed.",
                    "",
                ]
            )
        zf.writestr("README.txt", "\n".join(readme))
    buf.seek(0)
    return buf.getvalue(), {
        "ok": True,
        "year": y,
        "profile_id": profile_id,
        "readiness": readiness,
        "token": token_info,
        "filename": f"cpa_pack_{profile.slug}_{y}.zip",
    }
