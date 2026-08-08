"""Seed profiles and IRS-mapped Chart of Accounts from tax_coa.json."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from sqlalchemy.orm import Session

from decimal import Decimal

from financial_os.db import AppSettings, AppUser, Category, Profile
from financial_os.services.categorizer import seed_default_rules


def load_coa() -> dict[str, Any]:
    with resources.files("financial_os.data").joinpath("tax_coa.json").open(
        encoding="utf-8"
    ) as f:
        return json.load(f)


def seed_all(session: Session) -> None:
    coa = load_coa()
    _seed_profiles(session, coa["profiles"])
    session.flush()
    profiles = {p.slug: p for p in session.query(Profile).all()}

    if session.query(Category).count() == 0:
        for item in coa.get("system_categories", []):
            session.add(_category(item, scope="system", profile_id=None, is_system=True))

        for slug in ("ap_agency", "aib42"):
            pid = profiles[slug].id
            for item in coa.get("business_income", []):
                session.add(_category(item, scope="business", profile_id=pid))
            for item in coa.get("business_expenses", []):
                session.add(_category(item, scope="business", profile_id=pid))
            for item in coa.get("separately_stated", []):
                session.add(_category(item, scope="business", profile_id=pid))

        personal_id = profiles["personal"].id
        for item in coa.get("personal_budget", []):
            session.add(_category(item, scope="personal", profile_id=personal_id))
        for item in coa.get("personal_itemized", []):
            session.add(_category(item, scope="personal", profile_id=personal_id))
        for item in coa.get("personal_adjustments", []):
            session.add(_category(item, scope="personal", profile_id=personal_id))

    if session.query(AppSettings).count() == 0:
        session.add(
            AppSettings(
                id=1,
                safety_buffer=Decimal("1000"),
                never_negative_scope="checking",
                opportunity_cost_aware=True,
                debt_strategy="avalanche",
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


def _category(
    item: dict,
    *,
    scope: str,
    profile_id: int | None,
    is_system: bool = False,
) -> Category:
    code = item["code"]
    # Business categories are per-profile; uniquify codes
    if profile_id is not None and scope == "business":
        # look up profile slug via deferred — encode profile in code
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
        is_system=is_system,
    )
