"""Seed profiles and IRS-mapped Chart of Accounts from tax_coa.json."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from sqlalchemy.orm import Session

from decimal import Decimal

from honestspend.db import AppSettings, AppUser, Category, Profile
from honestspend.services.categorizer import seed_default_rules


def load_coa() -> dict[str, Any]:
    with resources.files("honestspend.data").joinpath("tax_coa.json").open(
        encoding="utf-8"
    ) as f:
        return json.load(f)


def make_category_row(
    item: dict,
    *,
    scope: str,
    profile_id: int | None,
    is_system: bool = False,
) -> Category:
    """Build a Category ORM row from a COA template item."""
    code = item["code"]
    # Per-profile categories uniquify codes so multiple businesses can share templates
    if profile_id is not None and scope in ("business", "child", "personal"):
        if scope == "business" or scope == "child":
            code = f"{code}__p{profile_id}"
        elif scope == "personal" and not code.startswith("PER_"):
            code = f"{code}__p{profile_id}"

    return Category(
        code=code,
        display_name=item["display_name"],
        profile_id=profile_id,
        scope=scope,
        tax_form=item.get("tax_form") or "none",
        tax_line=item.get("tax_line"),
        sch_c_line=str(item["sch_c_line"]) if item.get("sch_c_line") is not None else None,
        deductibility=str(item.get("deductibility") or "none"),
        partial_rule=item.get("partial_rule"),
        budget_group=item.get("budget_group") or "other",
        other_deduction_detail=bool(item.get("other_deduction_detail")),
        notes=item.get("notes"),
        legacy_excel=item.get("legacy_excel"),
    )


def seed_all(session: Session) -> None:
    """Fresh install: system categories + Personal entity only.

    Businesses and children are created via POST /api/profiles (Add Business / Add Child).
    """
    coa = load_coa()
    _seed_profiles(session, coa["profiles"])
    session.flush()
    profiles = {p.slug: p for p in session.query(Profile).all()}

    if session.query(Category).count() == 0:
        for item in coa.get("system_categories", []):
            session.add(
                make_category_row(item, scope="system", profile_id=None, is_system=True)
            )

        personal = profiles.get("personal")
        if personal:
            for pack in ("personal_budget", "personal_itemized", "personal_adjustments"):
                for item in coa.get(pack, []):
                    session.add(
                        make_category_row(
                            item, scope="personal", profile_id=personal.id
                        )
                    )

    if session.query(AppSettings).count() == 0:
        session.add(
            AppSettings(
                id=1,
                safety_buffer=Decimal("1000"),
                never_negative_scope="checking",
                opportunity_cost_aware=True,
                debt_strategy="avalanche",
                never_negative_enforcement="warn",
            )
        )

    session.flush()
    seed_default_rules(session)

    if session.query(AppUser).filter(AppUser.username == "owner").count() == 0:
        session.add(
            AppUser(
                username="owner",
                display_name="Owner",
                role="owner",
                active=True,
            )
        )

    session.commit()


def _coa_base_code(code: str) -> str:
    return (code or "").split("__p")[0]


def ensure_coa_categories(session: Session) -> int:
    """Add any new COA rows that shipped after this books file was created."""
    coa = load_coa()
    added = 0
    existing = {c.code for c in session.query(Category).all()}
    personal = session.query(Profile).filter(Profile.slug == "personal").first()
    if personal:
        for pack in ("personal_budget", "personal_itemized", "personal_adjustments"):
            for item in coa.get(pack, []):
                row = make_category_row(item, scope="personal", profile_id=personal.id)
                if row.code in existing:
                    continue
                session.add(row)
                existing.add(row.code)
                added += 1
    if session.query(Category).filter(Category.scope == "system").count() == 0:
        for item in coa.get("system_categories", []):
            row = make_category_row(item, scope="system", profile_id=None, is_system=True)
            if row.code in existing:
                continue
            session.add(row)
            existing.add(row.code)
            added += 1
    biz_packs = ("business_income", "business_expenses", "separately_stated")
    biz_by_code = {
        item["code"]: item
        for pack in biz_packs
        for item in coa.get(pack, [])
    }
    for prof in session.query(Profile).all():
        et = (prof.entity_type or "").lower()
        form = (prof.tax_form_primary or "").upper()
        if et not in ("business", "s_corp", "llc") and form not in ("1120S", "1065", "SCHC", "1120"):
            continue
        for pack in biz_packs:
            for item in coa.get(pack, []):
                row = make_category_row(item, scope="business", profile_id=prof.id)
                if row.code in existing:
                    continue
                session.add(row)
                existing.add(row.code)
                added += 1
    session.flush()
    legacy_names = {
        "Advertising": "Marketing",
        "Contract labor": "Services",
        "Business meals": "Meals 50%",
        "Rent": "Rent (office / property)",
    }
    for cat in session.query(Category).filter(Category.scope == "business").all():
        item = biz_by_code.get(_coa_base_code(cat.code))
        if not item:
            continue
        if cat.display_name in legacy_names:
            cat.display_name = legacy_names[cat.display_name]
        if item.get("partial_rule") and not cat.partial_rule:
            cat.partial_rule = item.get("partial_rule")
    if added:
        session.flush()
    return added


def _seed_profiles(session: Session, profiles: list[dict]) -> None:
    existing = {p.slug for p in session.query(Profile).all()}
    for p in profiles:
        if p["slug"] in existing:
            continue
        session.add(
            Profile(
                slug=p["slug"],
                display_name=p["display_name"],
                entity_type=p["entity_type"],
                tax_form_primary=p["tax_form_primary"],
                is_default=bool(p.get("is_default")),
            )
        )
