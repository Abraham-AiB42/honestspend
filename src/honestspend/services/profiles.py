"""Create and manage entity profiles (Personal, Business, Child)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from honestspend.db import Category, Profile
from honestspend.seed import load_coa, make_category_row


ENTITY_TYPES = frozenset({"personal", "individual", "business", "child"})

DEFAULT_TAX_FORM = {
    "personal": "1040",
    "individual": "1040",
    "business": "1120S",
    "child": "none",
}


def slugify(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")[:48] or "entity"
    return s


def unique_slug(session: Session, base: str) -> str:
    slug = slugify(base)
    if session.query(Profile).filter(Profile.slug == slug).count() == 0:
        return slug
    for i in range(2, 1000):
        candidate = f"{slug}-{i}"
        if session.query(Profile).filter(Profile.slug == candidate).count() == 0:
            return candidate
    raise ValueError("Could not allocate unique slug")


def normalize_entity_type(entity_type: str) -> str:
    et = (entity_type or "personal").strip().lower()
    if et == "individual":
        et = "personal"
    if et not in ("personal", "business", "child"):
        raise ValueError(
            f"entity_type must be personal, business, or child (got {entity_type!r})"
        )
    return et


def apply_coa_for_profile(session: Session, profile: Profile) -> int:
    """Attach COA template categories for a newly created profile. Returns count added."""
    coa = load_coa()
    et = normalize_entity_type(profile.entity_type)
    added = 0

    if et == "personal":
        packs = (
            ("personal_budget", "personal"),
            ("personal_itemized", "personal"),
            ("personal_adjustments", "personal"),
        )
    elif et == "business":
        packs = (
            ("business_income", "business"),
            ("business_expenses", "business"),
            ("separately_stated", "business"),
        )
    else:  # child
        packs = (("child_budget", "child"),)

    existing_codes = {c.code for c in session.query(Category.code).all()}
    # Category.code is scalar; query properly
    existing_codes = {c.code for c in session.query(Category).all()}

    for pack_key, scope in packs:
        for item in coa.get(pack_key, []):
            row = make_category_row(
                item, scope=scope, profile_id=profile.id, is_system=False
            )
            if row.code in existing_codes:
                continue
            session.add(row)
            existing_codes.add(row.code)
            added += 1

    session.flush()
    try:
        from honestspend.services.categorizer import seed_rules_for_profile

        seed_rules_for_profile(session, profile)
    except Exception:
        pass
    return added


def create_profile(
    session: Session,
    *,
    display_name: str,
    entity_type: str = "business",
    tax_form_primary: str | None = None,
    parent_profile_id: int | None = None,
    slug: str | None = None,
    is_default: bool = False,
) -> Profile:
    et = normalize_entity_type(entity_type)
    name = (display_name or "").strip()
    if not name:
        raise ValueError("display_name is required")

    if parent_profile_id is not None:
        parent = session.get(Profile, parent_profile_id)
        if not parent:
            raise ValueError("parent_profile_id not found")
        if et != "child":
            raise ValueError("parent_profile_id is only valid for child entities")

    form = (tax_form_primary or DEFAULT_TAX_FORM[et]).strip()
    if et == "child":
        form = "none"

    final_slug = unique_slug(session, slug or name)
    profile = Profile(
        slug=final_slug,
        display_name=name,
        entity_type=et if et != "personal" else "individual",
        tax_form_primary=form,
        is_default=is_default,
        parent_profile_id=parent_profile_id,
    )
    # Store entity_type as business|child|individual for clarity
    if et == "personal":
        profile.entity_type = "individual"
    session.add(profile)
    session.flush()
    apply_coa_for_profile(session, profile)
    return profile


def archive_profile(session: Session, profile_id: int) -> Profile:
    row = session.get(Profile, profile_id)
    if not row:
        raise ValueError("Profile not found")
    if row.is_default:
        raise ValueError("Cannot archive the default Personal profile")
    row.archived_at = datetime.now(tz=None)
    session.flush()
    return row


def list_active_profiles(session: Session) -> list[Profile]:
    return (
        session.query(Profile)
        .filter(Profile.archived_at.is_(None))
        .order_by(Profile.id)
        .all()
    )


def profile_to_dict(p: Profile) -> dict[str, Any]:
    return {
        "id": p.id,
        "slug": p.slug,
        "display_name": p.display_name,
        "entity_type": p.entity_type,
        "tax_form_primary": p.tax_form_primary,
        "is_default": bool(p.is_default),
        "parent_profile_id": p.parent_profile_id,
        "home_state": p.home_state,
        "multi_state": bool(p.multi_state),
        "filing_notes": p.filing_notes,
        "state_allocation_json": p.state_allocation_json,
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
    }
