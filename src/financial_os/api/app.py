from __future__ import annotations

import zipfile
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy.orm import Session

from financial_os import __version__
from financial_os.config import settings
from financial_os.config import settings as app_settings
from financial_os.db import (
    Account,
    AppSettings,
    Category,
    CategoryRule,
    PlaidItem,
    Profile,
    ScheduledItem,
    Transaction,
    init_db,
    make_engine,
    make_session_factory,
)
from financial_os.seed import seed_all
from financial_os.services.bank_csv import import_bank_csv
from financial_os.services.categorizer import (
    categorize_uncategorized,
    learn_rule_from_correction,
    suggest_category,
)
from financial_os.services.demo_seed import seed_demo_if_empty
from financial_os.services.excel_import import import_budget_xlsx
from financial_os.services.ifpp_service import ifpp_to_dict, run_ifpp
from financial_os.services.payoff import plan_card_payoff, plan_to_dict
from financial_os.engine.ifpp import CardView
from financial_os.services import plaid_service
from financial_os.services.tax_packet import build_tax_packet, packet_to_csv_files, write_tax_packet_dir

engine = make_engine()
SessionLocal = make_session_factory(engine)
WEB_DIR = Path(__file__).resolve().parents[3] / "web"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Non-loopback without allow is only safe if API keys are required
    if (
        not app_settings.is_loopback_host
        and not app_settings.allow_non_loopback
        and not app_settings.effective_require_api_key
    ):
        raise RuntimeError(
            f"Refusing to bind host={app_settings.host!r} without FOS_REQUIRE_API_KEY "
            "or FOS_ALLOW_NON_LOOPBACK=1"
        )

    init_db(engine)
    session = SessionLocal()
    try:
        seed_all(session)
        if app_settings.seed_demo:
            seed_demo_if_empty(session)
        session.commit()
    finally:
        session.close()

    try:
        from financial_os.services.auto_backup import start_auto_backup_loop

        start_auto_backup_loop(SessionLocal, check_seconds=900)
    except Exception:
        pass

    yield

    try:
        from financial_os.services.auto_backup import stop_auto_backup_loop

        stop_auto_backup_loop()
    except Exception:
        pass


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)


def get_db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.middleware("http")
async def permission_middleware(request: Request, call_next):
    """X-API-Key → role enforcement.

    Loopback single-user: no key = owner.
    Non-loopback or multi-user (2+ active users): key required.
    """
    path = request.url.path
    if not path.startswith("/api/") or path in ("/api/health",):
        return await call_next(request)
    token = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    session = SessionLocal()
    try:
        from financial_os.services.permissions import (
            capability_for_request,
            multi_user_mode,
            resolve_context,
        )

        need_key = app_settings.effective_require_api_key or multi_user_mode(session)
        if need_key and not token:
            return JSONResponse(
                {
                    "detail": (
                        "X-API-Key required "
                        "(multi-user mode, FOS_REQUIRE_API_KEY, or non-loopback bind). "
                        "Create a token via Users page or: honestspend token"
                    ),
                    "multi_user_mode": multi_user_mode(session),
                },
                status_code=401,
            )
        try:
            ctx = resolve_context(session, token)
        except PermissionError as e:
            return JSONResponse({"detail": str(e)}, status_code=401)
        cap = capability_for_request(request.method, path)
        if cap and not ctx.can(cap):
            return JSONResponse(
                {"detail": f"Role {ctx.role.value} cannot {cap}"},
                status_code=403,
            )
        request.state.access = ctx
        # Lite audit for mutating calls
        if request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                from financial_os.services.audit import log_event

                log_event(
                    session,
                    username=ctx.username,
                    role=ctx.role.value,
                    action=request.method.upper(),
                    path=path[:256],
                )
                session.commit()
            except Exception:
                session.rollback()
    finally:
        session.close()
    return await call_next(request)


# --- Schemas ---


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    display_name: str
    entity_type: str
    tax_form_primary: str
    is_default: bool
    parent_profile_id: int | None = None
    home_state: str | None = None
    multi_state: bool = False
    filing_notes: str | None = None
    state_allocation_json: str | None = None
    archived_at: datetime | None = None


class ProfileCreate(BaseModel):
    display_name: str
    entity_type: str = "business"  # personal | business | child
    tax_form_primary: str | None = None
    parent_profile_id: int | None = None
    slug: str | None = None


class ProfilePatch(BaseModel):
    display_name: str | None = None
    home_state: str | None = None
    multi_state: bool | None = None
    filing_notes: str | None = None
    state_allocation_json: str | None = None
    tax_form_primary: str | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    display_name: str
    profile_id: int | None
    scope: str
    tax_form: str
    tax_line: str | None
    sch_c_line: str | None
    deductibility: str
    partial_rule: str | None
    budget_group: str
    notes: str | None


class AccountIn(BaseModel):
    profile_id: int
    kind: str
    nickname: str
    institution: str | None = None
    current_balance: Decimal = Decimal("0")
    available_credit: Decimal | None = None
    credit_limit: Decimal | None = None
    is_cash_for_ifpp: bool = False
    statement_close_day: int | None = None
    payment_due_day: int | None = None
    apr: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_end_date: date | None = None
    promo_balance: Decimal | None = None
    min_payment: Decimal | None = None
    rewards_program: str | None = None
    opened_date: date | None = None
    priority_rank: int = 100
    apy: Decimal | None = None  # cash/savings yield e.g. 0.06


class AccountPatch(BaseModel):
    """Partial update for in-app account/card management."""

    nickname: str | None = None
    institution: str | None = None
    kind: str | None = None
    current_balance: Decimal | None = None
    available_credit: Decimal | None = None
    credit_limit: Decimal | None = None
    is_cash_for_ifpp: bool | None = None
    statement_close_day: int | None = None
    payment_due_day: int | None = None
    apr: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_end_date: date | None = None
    promo_balance: Decimal | None = None
    min_payment: Decimal | None = None
    rewards_program: str | None = None
    opened_date: date | None = None
    priority_rank: int | None = None
    apy: Decimal | None = None
    institution_balance: Decimal | None = None


class AccountOut(AccountIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plaid_account_id: str | None = None
    institution_balance: Decimal | None = None
    last_reconciled_at: datetime | None = None


class TransactionIn(BaseModel):
    profile_id: int
    account_id: int
    category_id: int | None = None
    txn_date: date
    amount: Decimal
    payee: str | None = None
    memo: str | None = None
    status: str = "cleared"
    is_transfer: bool = False
    # When never_negative_enforcement=warn, set true to allow checking to go negative
    confirm_unsafe: bool = False


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    account_id: int
    category_id: int | None = None
    txn_date: date
    amount: Decimal
    payee: str | None = None
    memo: str | None = None
    status: str = "cleared"
    is_transfer: bool = False
    fee_status: str | None = None


class TransactionPatch(BaseModel):
    category_id: int | None = None
    payee: str | None = None
    memo: str | None = None
    amount: Decimal | None = None
    txn_date: date | None = None
    is_transfer: bool | None = None


class ScheduledIn(BaseModel):
    profile_id: int
    name: str
    amount: Decimal
    next_date: date
    end_date: date | None = None
    cadence: str = "monthly"
    certainty: str = "fixed"
    kind: str = "expense"  # expense | income
    account_id: int | None = None
    category_id: int | None = None
    notes: str | None = None
    active: bool = True

    @field_validator("kind")
    @classmethod
    def kind_ok(cls, v: str) -> str:
        v = (v or "expense").lower()
        if v not in ("expense", "income"):
            raise ValueError("kind must be expense or income")
        return v

    @model_validator(mode="after")
    def normalize_amount_and_account(self):
        # Expenses are negative, income positive
        if self.kind == "expense" and self.amount > 0:
            self.amount = -abs(self.amount)
        if self.kind == "income" and self.amount < 0:
            self.amount = abs(self.amount)
        if self.kind == "expense" and not self.account_id:
            raise ValueError("Recurring expenses require an account or card (account_id)")
        if self.end_date and self.end_date < self.next_date:
            raise ValueError("end_date cannot be before next_date")
        return self


class ScheduledOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    name: str
    amount: Decimal
    next_date: date
    end_date: date | None = None
    cadence: str = "monthly"
    certainty: str = "fixed"
    kind: str = "expense"
    account_id: int | None = None
    category_id: int | None = None
    notes: str | None = None
    active: bool = True
    ended_at: datetime | None = None
    ended_reason: str | None = None
    # enriched
    account_nickname: str | None = None
    account_kind: str | None = None
    category_name: str | None = None
    profile_name: str | None = None


class ScheduledEndIn(BaseModel):
    end_date: date | None = None  # default today
    reason: str | None = None


class SettingsIn(BaseModel):
    ifpp_mode: str = "conservative"
    safety_buffer: Decimal = Decimal("1000")
    never_negative_scope: str = "checking"
    utilization_warn_soft: int = 10
    utilization_warn_hard: int = 30
    horizon_days: int = 45
    auto_categorize_on_import: bool = True
    onboarding_complete: bool | None = None
    product_name: str | None = None
    debt_strategy: str | None = None
    debt_extra_monthly: Decimal | None = None
    opportunity_rate: Decimal | None = None
    opportunity_cost_aware: bool | None = None
    opportunity_tax_rate: Decimal | None = None
    credit_on_time_rate: Decimal | None = None
    credit_late_30: int | None = None
    credit_late_60: int | None = None
    credit_late_90: int | None = None
    credit_hard_inquiries: int | None = None
    credit_new_accounts: int | None = None
    credit_reported_vantage: int | None = None
    tax_vault_enabled: bool | None = None
    tax_vault_balance: Decimal | None = None
    tax_vault_income_rate: Decimal | None = None
    income_cliff_enabled: bool | None = None
    income_cliff_factor: Decimal | None = None
    auto_backup_enabled: bool | None = None
    auto_backup_interval_hours: int | None = None
    auto_backup_keep: int | None = None
    ifpp_scope: str | None = None  # entity | group
    ifpp_cleared_only: bool | None = None
    never_negative_enforcement: str | None = None  # off | warn | hard
    import_reminder_cadence: str | None = None  # off | daily | weekly | monthly
    import_reminder_focus: str | None = None  # transactions | statements | both
    import_last_at: datetime | None = None
    import_reminder_snooze_until: date | None = None
    budget_reserve_enabled: bool | None = None
    budget_week_starts_on: int | None = None
    budget_workdays: int | None = None


class SettingsPatch(BaseModel):
    """Partial settings update — only provided fields are applied."""

    model_config = ConfigDict(extra="ignore")

    ifpp_mode: str | None = None
    safety_buffer: Decimal | None = None
    never_negative_scope: str | None = None
    utilization_warn_soft: int | None = None
    utilization_warn_hard: int | None = None
    horizon_days: int | None = None
    auto_categorize_on_import: bool | None = None
    onboarding_complete: bool | None = None
    product_name: str | None = None
    debt_strategy: str | None = None
    debt_extra_monthly: Decimal | None = None
    opportunity_rate: Decimal | None = None
    opportunity_cost_aware: bool | None = None
    opportunity_tax_rate: Decimal | None = None
    credit_on_time_rate: Decimal | None = None
    credit_late_30: int | None = None
    credit_late_60: int | None = None
    credit_late_90: int | None = None
    credit_hard_inquiries: int | None = None
    credit_new_accounts: int | None = None
    credit_reported_vantage: int | None = None
    tax_vault_enabled: bool | None = None
    tax_vault_balance: Decimal | None = None
    tax_vault_income_rate: Decimal | None = None
    income_cliff_enabled: bool | None = None
    income_cliff_factor: Decimal | None = None
    auto_backup_enabled: bool | None = None
    auto_backup_interval_hours: int | None = None
    auto_backup_keep: int | None = None
    ifpp_scope: str | None = None
    ifpp_cleared_only: bool | None = None
    never_negative_enforcement: str | None = None
    import_reminder_cadence: str | None = None
    import_reminder_focus: str | None = None
    import_last_at: datetime | None = None
    import_reminder_snooze_until: date | None = None
    budget_reserve_enabled: bool | None = None
    budget_week_starts_on: int | None = None
    budget_workdays: int | None = None


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = 1
    ifpp_mode: str = "conservative"
    safety_buffer: Decimal = Decimal("1000")
    never_negative_scope: str = "checking"
    utilization_warn_soft: int = 10
    utilization_warn_hard: int = 30
    horizon_days: int = 45
    auto_categorize_on_import: bool = True
    onboarding_complete: bool = False
    product_name: str = "HonestSpend"
    debt_strategy: str = "avalanche"
    debt_extra_monthly: Decimal = Decimal("0")
    opportunity_rate: Decimal | None = None
    opportunity_cost_aware: bool = True
    opportunity_tax_rate: Decimal | None = None
    credit_on_time_rate: Decimal = Decimal("1")
    credit_late_30: int = 0
    credit_late_60: int = 0
    credit_late_90: int = 0
    credit_hard_inquiries: int = 0
    credit_new_accounts: int = 0
    credit_reported_vantage: int | None = None
    tax_vault_enabled: bool = True
    tax_vault_balance: Decimal = Decimal("0")
    tax_vault_income_rate: Decimal | None = None
    income_cliff_enabled: bool = False
    income_cliff_factor: Decimal = Decimal("1")
    auto_backup_enabled: bool = True
    auto_backup_interval_hours: int = 24
    auto_backup_keep: int = 14
    auto_backup_last_at: datetime | None = None
    ifpp_scope: str = "entity"
    ifpp_cleared_only: bool = True
    never_negative_enforcement: str = "warn"
    import_reminder_cadence: str = "weekly"
    import_reminder_focus: str = "transactions"
    import_last_at: datetime | None = None
    import_reminder_snooze_until: date | None = None
    budget_reserve_enabled: bool = True
    budget_week_starts_on: int = 0
    budget_workdays: int = 31


class QuickSetupIn(BaseModel):
    profile_slug: str = "personal"
    cash_name: str = "Primary checking"
    cash_balance: Decimal = Decimal("0")
    cash_institution: str | None = None
    card_name: str | None = None
    card_balance: Decimal = Decimal("0")
    card_limit: Decimal | None = None
    card_due_day: int | None = None
    card_close_day: int | None = None
    card_promo_apr: Decimal | None = None
    card_promo_end: str | None = None
    safety_buffer: Decimal = Decimal("1000")
    ifpp_mode: str = "conservative"


class ImportPathIn(BaseModel):
    path: str
    profile_slug: str = "personal"
    sheet_name: str = "Budget"
    since: date | None = None
    dry_run: bool = False


# --- Routes ---


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "app": settings.app_name, "product": "HonestSpend"}


# --- License (buy once / all clients; local-first) ---


class LicenseActivateIn(BaseModel):
    key: str
    email: str | None = None


class LicenseStoreIn(BaseModel):
    is_active: bool
    store_kind: str = "ms_store"
    is_trial: bool = False
    store_sku: str | None = None
    detail: str | None = None


@app.get("/api/license")
def license_status():
    from financial_os.services.license_service import get_status

    return get_status()


@app.post("/api/license/activate")
def license_activate(body: LicenseActivateIn):
    from financial_os.services.license_service import activate_key

    try:
        return activate_key(body.key, body.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/license/store")
def license_store(body: LicenseStoreIn):
    """Client posts Microsoft Store (or other) entitlement after StoreContext check."""
    from financial_os.services.license_service import activate_store

    return activate_store(
        is_active=body.is_active,
        store_kind=body.store_kind,
        is_trial=body.is_trial,
        store_sku=body.store_sku,
        detail=body.detail,
    )


# --- Period budgets (daily / weekly / monthly) ---


class BudgetRuleIn(BaseModel):
    profile_id: int
    category_id: int
    period: str = "monthly"  # daily|weekly|monthly
    amount: Decimal
    name: str | None = None
    active_weekdays: int | None = None
    week_starts_on: int | None = None
    source: str = "manual"
    notes: str | None = None


class BudgetAcceptSuggestionIn(BaseModel):
    profile_id: int
    category_id: int
    period: str
    amount: Decimal | None = None  # default = engine suggestion
    name: str | None = None
    active_weekdays: int | None = None


class BudgetCutIn(BaseModel):
    budget_rule_id: int
    kind: str
    params: dict | None = None
    note: str | None = None


@app.get("/api/budgets")
def budgets_list(profile_id: int | None = None, db: Session = Depends(get_db)):
    from financial_os.services.budget_service import list_rules

    rows = list_rules(db, profile_id)
    out = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "profile_id": r.profile_id,
                "category_id": r.category_id,
                "name": r.name,
                "period": r.period,
                "amount": str(r.amount),
                "active_weekdays": r.active_weekdays,
                "week_starts_on": r.week_starts_on,
                "active": r.active,
                "source": r.source,
                "notes": r.notes,
            }
        )
    return {"items": out}


@app.get("/api/budgets/status")
def budgets_status_api(
    profile_id: int | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.budget_service import budgets_status

    return budgets_status(db, profile_id=profile_id, as_of=as_of)


@app.get("/api/budgets/suggestions")
def budgets_suggestions_api(
    profile_id: int | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.budget_service import suggestions

    return suggestions(db, profile_id=profile_id, as_of=as_of)


@app.post("/api/budgets")
def budgets_create(body: BudgetRuleIn, db: Session = Depends(get_db)):
    from financial_os.services.budget_service import create_rule

    try:
        rule = create_rule(
            db,
            profile_id=body.profile_id,
            category_id=body.category_id,
            period=body.period,
            amount=body.amount,
            name=body.name,
            active_weekdays=body.active_weekdays,
            week_starts_on=body.week_starts_on,
            source=body.source,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "id": rule.id,
        "profile_id": rule.profile_id,
        "category_id": rule.category_id,
        "period": rule.period,
        "amount": str(rule.amount),
        "name": rule.name,
        "source": rule.source,
    }


@app.post("/api/budgets/suggestions/accept")
def budgets_accept_suggestion(body: BudgetAcceptSuggestionIn, db: Session = Depends(get_db)):
    from financial_os.services.budget_service import create_rule, suggest_for_category

    amt = body.amount
    if amt is None:
        sug = suggest_for_category(
            db,
            profile_id=body.profile_id,
            category_id=body.category_id,
            period=body.period,
        )
        amt = Decimal(sug["suggested_amount"])
    rule = create_rule(
        db,
        profile_id=body.profile_id,
        category_id=body.category_id,
        period=body.period,
        amount=amt,
        name=body.name,
        active_weekdays=body.active_weekdays,
        source="accepted_suggestion",
    )
    return {"id": rule.id, "amount": str(rule.amount), "period": rule.period}


class BudgetSeedIn(BaseModel):
    profile_id: int | None = None
    max_rules: int = 10
    only_if_empty: bool = False


@app.post("/api/budgets/seed-from-history")
def budgets_seed_from_history(body: BudgetSeedIn | None = None, db: Session = Depends(get_db)):
    """Create daily/weekly/monthly plans from top historical spend (Excel-style defaults)."""
    from financial_os.services.budget_service import seed_from_history

    body = body or BudgetSeedIn()
    return seed_from_history(
        db,
        profile_id=body.profile_id,
        max_rules=body.max_rules,
        only_if_empty=body.only_if_empty,
    )


@app.get("/api/budgets/cuts")
def budgets_cuts_preview(
    profile_id: int | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.budget_service import preview_cuts

    return {"offers": preview_cuts(db, profile_id=profile_id, as_of=as_of)}


@app.post("/api/budgets/cuts/apply")
def budgets_cuts_apply(body: BudgetCutIn, db: Session = Depends(get_db)):
    from financial_os.services.budget_service import apply_cut

    try:
        return apply_cut(
            db,
            budget_rule_id=body.budget_rule_id,
            kind=body.kind,
            params=body.params,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/budgets/{rule_id}")
def budgets_delete(rule_id: int, db: Session = Depends(get_db)):
    from financial_os.db import BudgetRule

    row = db.get(BudgetRule, rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    row.active = False
    return {"ok": True}


@app.post("/api/license/clear")
def license_clear():
    from financial_os.services.license_service import clear_license

    return clear_license()


@app.post("/api/license/refresh")
def license_refresh():
    from financial_os.services.license_service import refresh_license

    try:
        return refresh_license()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/onboarding")
def onboarding_status(db: Session = Depends(get_db)):
    from financial_os.services.onboarding import get_onboarding_status

    s = get_onboarding_status(db)
    return {
        "complete": s.complete,
        "has_cash_account": s.has_cash_account,
        "has_credit_account": s.has_credit_account,
        "has_recurring": s.has_recurring,
        "account_count": s.account_count,
        "product_name": s.product_name,
        "needs_setup": not s.complete and s.account_count == 0,
    }


@app.post("/api/onboarding/complete")
def onboarding_complete(db: Session = Depends(get_db)):
    from financial_os.services.onboarding import complete_onboarding

    complete_onboarding(db)
    return {"ok": True, "onboarding_complete": True}


@app.post("/api/onboarding/quick-setup")
def onboarding_quick_setup(body: QuickSetupIn, db: Session = Depends(get_db)):
    from financial_os.services.onboarding import apply_quick_setup

    try:
        result = apply_quick_setup(db, **body.model_dump())
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


class FirstRunIn(BaseModel):
    profile_slug: str = "personal"
    cash_name: str = "Primary checking"
    cash_balance: Decimal = Decimal("0")
    cash_institution: str | None = None
    safety_buffer: Decimal = Decimal("1000")
    ifpp_mode: str = "conservative"
    card_name: str | None = None
    card_balance: Decimal = Decimal("0")
    card_limit: Decimal | None = None
    card_due_day: int | None = 15
    card_promo_end: str | None = None
    bill_name: str | None = None
    bill_amount: Decimal | None = None
    bill_next_date: date | None = None
    # Freeware money-in reminders
    import_reminder_cadence: str = "weekly"  # off | daily | weekly | monthly
    import_reminder_focus: str = "transactions"  # transactions | statements | both


@app.post("/api/onboarding/first-run")
def onboarding_first_run(body: FirstRunIn, db: Session = Depends(get_db)):
    """Atomic first-run wizard: cash + optional card + optional bill."""
    from financial_os.services.onboarding import apply_first_run

    data = body.model_dump()
    if data.get("bill_next_date") is not None:
        data["bill_next_date"] = data["bill_next_date"].isoformat()
    try:
        result = apply_first_run(db, **data)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/profiles", response_model=list[ProfileOut])
def list_profiles(
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Profile)
    if not include_archived:
        q = q.filter(Profile.archived_at.is_(None))
    return q.order_by(Profile.id).all()


@app.post("/api/profiles", response_model=ProfileOut)
def create_profile_api(body: ProfileCreate, db: Session = Depends(get_db)):
    from financial_os.services.profiles import create_profile

    try:
        row = create_profile(
            db,
            display_name=body.display_name,
            entity_type=body.entity_type,
            tax_form_primary=body.tax_form_primary,
            parent_profile_id=body.parent_profile_id,
            slug=body.slug,
        )
        db.flush()
        db.refresh(row)
        return row
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/profiles/{profile_id}/archive", response_model=ProfileOut)
def archive_profile_api(profile_id: int, db: Session = Depends(get_db)):
    from financial_os.services.profiles import archive_profile

    try:
        row = archive_profile(db, profile_id)
        db.flush()
        db.refresh(row)
        return row
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/profiles/{profile_id}", response_model=ProfileOut)
def patch_profile(profile_id: int, body: ProfilePatch, db: Session = Depends(get_db)):
    row = db.get(Profile, profile_id)
    if not row:
        raise HTTPException(404, "Profile not found")
    data = body.model_dump(exclude_unset=True)
    if "home_state" in data and data["home_state"]:
        data["home_state"] = str(data["home_state"]).strip().upper()[:8]
    if "state_allocation_json" in data and data["state_allocation_json"]:
        import json

        try:
            json.loads(data["state_allocation_json"])
        except Exception as e:
            raise HTTPException(400, f"state_allocation_json must be JSON: {e}") from e
    for k, v in data.items():
        setattr(row, k, v)
    db.flush()
    db.refresh(row)
    return row


@app.get("/api/categories", response_model=list[CategoryOut])
def list_categories(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Category)
    if profile_id is not None:
        q = q.filter((Category.profile_id == profile_id) | (Category.profile_id.is_(None)))
    if scope:
        q = q.filter(Category.scope == scope)
    return q.order_by(Category.budget_group, Category.display_name).all()


@app.get("/api/accounts", response_model=list[AccountOut])
def list_accounts(
    profile_id: Optional[int] = None,
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(Account)
    if profile_id is not None:
        q = q.filter(Account.profile_id == profile_id)
    if not include_archived:
        q = q.filter(Account.archived_at.is_(None))
    return q.order_by(Account.id).all()


@app.post("/api/accounts", response_model=AccountOut)
def create_account(body: AccountIn, db: Session = Depends(get_db)):
    if not db.get(Profile, body.profile_id):
        raise HTTPException(404, "Profile not found")
    row = Account(**body.model_dump())
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


@app.put("/api/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, body: AccountIn, db: Session = Depends(get_db)):
    row = db.get(Account, account_id)
    if not row:
        raise HTTPException(404, "Account not found")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    _sync_credit_available(row)
    db.flush()
    db.refresh(row)
    return row


@app.patch("/api/accounts/{account_id}", response_model=AccountOut)
def patch_account(account_id: int, body: AccountPatch, db: Session = Depends(get_db)):
    row = db.get(Account, account_id)
    if not row:
        raise HTTPException(404, "Account not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    _sync_credit_available(row)
    db.flush()
    db.refresh(row)
    return row


def _sync_credit_available(row: Account) -> None:
    if row.kind == "credit" and row.credit_limit is not None:
        bal = Decimal(row.current_balance or 0)
        row.available_credit = Decimal(row.credit_limit) - bal
        row.is_cash_for_ifpp = False


@app.post("/api/accounts/{account_id}/archive")
def archive_account(account_id: int, db: Session = Depends(get_db)):
    row = db.get(Account, account_id)
    if not row:
        raise HTTPException(404, "Account not found")
    row.archived_at = datetime.now()
    row.is_cash_for_ifpp = False
    db.flush()
    return {
        "ok": True,
        "archived": True,
        "account_id": row.id,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


@app.post("/api/accounts/{account_id}/unarchive")
def unarchive_account(account_id: int, db: Session = Depends(get_db)):
    row = db.get(Account, account_id)
    if not row:
        raise HTTPException(404, "Account not found")
    row.archived_at = None
    db.flush()
    return {"ok": True, "archived": False, "account_id": row.id}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    row = db.get(Account, account_id)
    if not row:
        raise HTTPException(404, "Account not found")
    # Keep ledger history; just detach or block delete if txns exist
    txn_count = db.query(Transaction).filter(Transaction.account_id == account_id).count()
    if txn_count:
        raise HTTPException(
            400,
            f"Account has {txn_count} transactions. Use POST /api/accounts/{account_id}/archive instead.",
        )
    db.delete(row)
    return {"ok": True}


@app.get("/api/transactions", response_model=list[TransactionOut])
def list_transactions(
    profile_id: Optional[int] = None,
    uncategorized: bool = False,
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if profile_id is not None:
        q = q.filter(Transaction.profile_id == profile_id)
    if uncategorized:
        q = q.filter(Transaction.category_id.is_(None))
    return q.order_by(Transaction.txn_date.desc(), Transaction.id.desc()).limit(limit).all()


@app.post("/api/transactions", response_model=TransactionOut)
def create_transaction(body: TransactionIn, db: Session = Depends(get_db)):
    from financial_os.services.never_neg import WouldGoNegative, check_cash_outflow

    data = body.model_dump(exclude={"confirm_unsafe"})
    acct = db.get(Account, body.account_id)
    try:
        check_cash_outflow(
            db,
            account=acct,
            amount=Decimal(body.amount),
            confirm_unsafe=bool(body.confirm_unsafe),
        )
    except WouldGoNegative as e:
        raise HTTPException(status_code=409, detail=e.payload) from e

    row = Transaction(**data)
    db.add(row)
    if acct:
        from financial_os.services.account_balance import apply_amount_to_account

        apply_amount_to_account(acct, body.amount)
    db.flush()
    db.refresh(row)
    return row


class VoidTxnIn(BaseModel):
    reason: str | None = None


@app.post("/api/transactions/{txn_id}/void")
def void_transaction_api(txn_id: int, body: VoidTxnIn | None = None, db: Session = Depends(get_db)):
    from financial_os.services.txn_void import void_transaction

    try:
        return void_transaction(db, txn_id, reason=(body.reason if body else None))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.patch("/api/transactions/{txn_id}", response_model=TransactionOut)
def patch_transaction(
    txn_id: int,
    body: TransactionPatch,
    learn: bool = True,
    db: Session = Depends(get_db),
):
    row = db.get(Transaction, txn_id)
    if not row:
        raise HTTPException(404, "Transaction not found")
    if (row.status or "").lower() == "void":
        raise HTTPException(400, "Transaction is void — cannot edit")
    data = body.model_dump(exclude_unset=True)
    prev_cat = row.category_id
    for k, v in data.items():
        setattr(row, k, v)
    # Learn merchant rule when user sets/changes category
    if learn and body.category_id and body.category_id != prev_cat:
        learn_rule_from_correction(db, row, body.category_id)
    db.flush()
    db.refresh(row)
    return row


def _enrich_scheduled(db: Session, row: ScheduledItem) -> dict:
    acct = db.get(Account, row.account_id) if row.account_id else None
    cat = db.get(Category, row.category_id) if row.category_id else None
    prof = db.get(Profile, row.profile_id)
    kind = getattr(row, "kind", None) or ("expense" if Decimal(row.amount) < 0 else "income")
    return {
        "id": row.id,
        "profile_id": row.profile_id,
        "name": row.name,
        "amount": row.amount,
        "next_date": row.next_date,
        "end_date": getattr(row, "end_date", None),
        "cadence": row.cadence,
        "certainty": row.certainty,
        "kind": kind,
        "account_id": row.account_id,
        "category_id": row.category_id,
        "notes": getattr(row, "notes", None),
        "active": row.active,
        "ended_at": getattr(row, "ended_at", None),
        "ended_reason": getattr(row, "ended_reason", None),
        "account_nickname": acct.nickname if acct else None,
        "account_kind": acct.kind if acct else None,
        "category_name": cat.display_name if cat else None,
        "profile_name": prof.display_name if prof else None,
    }


def _validate_scheduled_account(db: Session, body: ScheduledIn) -> None:
    if not body.account_id:
        return
    acct = db.get(Account, body.account_id)
    if not acct:
        raise HTTPException(400, "account_id not found")
    if acct.profile_id != body.profile_id:
        raise HTTPException(400, "Account must belong to the same profile")


@app.get("/api/scheduled", response_model=list[ScheduledOut])
def list_scheduled(
    active_only: bool = True,
    kind: Optional[str] = None,
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(ScheduledItem)
    if active_only:
        q = q.filter(ScheduledItem.active.is_(True))
    if profile_id is not None:
        q = q.filter(ScheduledItem.profile_id == profile_id)
    rows = q.order_by(ScheduledItem.next_date).all()
    out = [_enrich_scheduled(db, r) for r in rows]
    if kind:
        out = [r for r in out if r["kind"] == kind]
    return out


@app.get("/api/scheduled/{item_id}", response_model=ScheduledOut)
def get_scheduled(item_id: int, db: Session = Depends(get_db)):
    row = db.get(ScheduledItem, item_id)
    if not row:
        raise HTTPException(404, "Scheduled item not found")
    return _enrich_scheduled(db, row)


@app.post("/api/scheduled", response_model=ScheduledOut)
def create_scheduled(body: ScheduledIn, db: Session = Depends(get_db)):
    if not db.get(Profile, body.profile_id):
        raise HTTPException(404, "Profile not found")
    _validate_scheduled_account(db, body)
    data = body.model_dump()
    row = ScheduledItem(**data)
    db.add(row)
    db.flush()
    db.refresh(row)
    return _enrich_scheduled(db, row)


@app.put("/api/scheduled/{item_id}", response_model=ScheduledOut)
def put_scheduled(item_id: int, body: ScheduledIn, db: Session = Depends(get_db)):
    row = db.get(ScheduledItem, item_id)
    if not row:
        raise HTTPException(404, "Scheduled item not found")
    _validate_scheduled_account(db, body)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    # Reactivating via edit clears end metadata if active
    if body.active:
        row.ended_at = None
        row.ended_reason = None
    db.flush()
    db.refresh(row)
    return _enrich_scheduled(db, row)


@app.patch("/api/scheduled/{item_id}", response_model=ScheduledOut)
def patch_scheduled(item_id: int, body: ScheduledIn, db: Session = Depends(get_db)):
    return put_scheduled(item_id, body, db)


@app.post("/api/scheduled/{item_id}/end", response_model=ScheduledOut)
def end_scheduled(item_id: int, body: ScheduledEndIn | None = None, db: Session = Depends(get_db)):
    """Stop a recurring expense/income. Sets active=False and end_date."""
    row = db.get(ScheduledItem, item_id)
    if not row:
        raise HTTPException(404, "Scheduled item not found")
    body = body or ScheduledEndIn()
    end = body.end_date or date.today()
    row.active = False
    row.end_date = end
    row.ended_at = datetime.now()
    row.ended_reason = body.reason or "Ended by user"
    db.flush()
    db.refresh(row)
    return _enrich_scheduled(db, row)


@app.delete("/api/scheduled/{item_id}")
def delete_scheduled(item_id: int, db: Session = Depends(get_db)):
    """Alias for end — recurring items are ended, not hard-deleted."""
    end_scheduled(item_id, ScheduledEndIn(reason="Ended via delete"), db)
    return {"ok": True, "ended": True}


@app.get("/api/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        db.add(row)
        db.flush()
    return row


@app.put("/api/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn, db: Session = Depends(get_db)):
    """Full-ish replace using provided fields (prefer PATCH for partial updates)."""
    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        db.add(row)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None and k in ("onboarding_complete", "product_name"):
            continue
        if v is not None or k not in ("onboarding_complete", "product_name"):
            setattr(row, k, v)
    db.flush()
    db.refresh(row)
    return row


@app.patch("/api/settings", response_model=SettingsOut)
def patch_settings(body: SettingsPatch, db: Session = Depends(get_db)):
    """Apply only fields present in the request body."""
    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        db.add(row)
    data = body.model_dump(exclude_unset=True)
    if "ifpp_scope" in data and data["ifpp_scope"] is not None:
        if data["ifpp_scope"] not in ("entity", "group"):
            raise HTTPException(400, "ifpp_scope must be entity or group")
    if "never_negative_enforcement" in data and data["never_negative_enforcement"] is not None:
        if data["never_negative_enforcement"] not in ("off", "warn", "hard"):
            raise HTTPException(400, "never_negative_enforcement must be off, warn, or hard")
    for k, v in data.items():
        setattr(row, k, v)
    db.flush()
    db.refresh(row)
    return row


@app.get("/api/ifpp")
def get_ifpp(
    mode: Optional[str] = None,
    as_of: Optional[date] = None,
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Spendable / IFPP. Default scope=entity (silo). Use scope=group for combined."""
    return ifpp_to_dict(
        run_ifpp(db, as_of=as_of, mode=mode, profile_id=profile_id, scope=scope),
        session=db,
    )


class SimulateOutflow(BaseModel):
    on_date: date | None = None  # not named "date" — shadows datetime.date in annotations
    amount: Decimal
    name: str | None = None


class SimulateIn(BaseModel):
    extra_outflows: list[SimulateOutflow] = []
    profile_id: int | None = None
    scope: str | None = None
    mode: str | None = None


@app.post("/api/ifpp/simulate")
def ifpp_simulate(body: SimulateIn, db: Session = Depends(get_db)):
    from financial_os.services.ifpp_simulate import simulate_ifpp

    extras = []
    for e in body.extra_outflows:
        d = e.model_dump()
        d["date"] = d.pop("on_date", None)
        extras.append(d)
    return simulate_ifpp(
        db,
        extra_outflows=extras,
        profile_id=body.profile_id,
        scope=body.scope,
        mode=body.mode,
    )


@app.get("/api/transfers/candidates")
def transfer_candidates(
    days: int = Query(7, ge=1, le=60),
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.transfer_match import find_transfer_candidates

    return find_transfer_candidates(db, days=days, profile_id=profile_id)


class TransferConfirmIn(BaseModel):
    out_txn_id: int
    in_txn_id: int


@app.post("/api/transfers/confirm")
def transfer_confirm(body: TransferConfirmIn, db: Session = Depends(get_db)):
    from financial_os.services.transfer_match import confirm_transfer_pair

    try:
        return confirm_transfer_pair(db, out_txn_id=body.out_txn_id, in_txn_id=body.in_txn_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/import/presets")
def import_presets_list(db: Session = Depends(get_db)):
    from financial_os.services.import_presets import list_presets

    return {"presets": list_presets(db)}


class ImportPresetIn(BaseModel):
    institution_key: str
    amount_sign: str = "bank"
    mapping: dict | None = None
    notes: str | None = None
    account_id: int | None = None


@app.put("/api/import/presets")
def import_presets_put(body: ImportPresetIn, db: Session = Depends(get_db)):
    from financial_os.services.import_presets import upsert_preset

    try:
        return upsert_preset(
            db,
            institution_key=body.institution_key,
            amount_sign=body.amount_sign,
            mapping_json=body.mapping,
            notes=body.notes,
            account_id=body.account_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/capital-desk")
def capital_desk(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Where the next dollar should go — fiscal first, options + reasoning."""
    from financial_os.services.capital_desk import build_capital_desk

    return build_capital_desk(db, profile_id=profile_id, scope=scope)


class TaxVaultIn(BaseModel):
    balance: Decimal | None = None
    enabled: bool | None = None
    income_rate: Decimal | None = None
    clear_income_rate: bool = False


class TaxVaultAdjustIn(BaseModel):
    delta: Decimal  # + set aside, - release to spendable
    note: str | None = None


@app.get("/api/tax-vault")
def tax_vault_get(db: Session = Depends(get_db)):
    from financial_os.services.tax_vault import get_tax_vault

    return get_tax_vault(db)


@app.put("/api/tax-vault")
def tax_vault_put(body: TaxVaultIn, db: Session = Depends(get_db)):
    from financial_os.services.tax_vault import set_tax_vault

    try:
        return set_tax_vault(
            db,
            balance=body.balance,
            enabled=body.enabled,
            income_rate=body.income_rate,
            clear_income_rate=body.clear_income_rate,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/tax-vault/adjust")
def tax_vault_adjust(body: TaxVaultAdjustIn, db: Session = Depends(get_db)):
    from financial_os.services.tax_vault import adjust_tax_vault

    return adjust_tax_vault(db, body.delta, note=body.note)


class PrePurchaseIn(BaseModel):
    amount: Decimal
    prefer: str = "auto"  # auto | cash | card
    account_id: int | None = None
    profile_id: int | None = None
    scope: str | None = None
    category_id: int | None = None


@app.post("/api/pre-purchase")
def pre_purchase(body: PrePurchaseIn, db: Session = Depends(get_db)):
    from financial_os.services.pre_purchase import check_purchase

    return check_purchase(
        db,
        amount=body.amount,
        prefer=body.prefer,
        account_id=body.account_id,
        profile_id=body.profile_id,
        scope=body.scope,
        category_id=body.category_id,
    )


class RescueIn(BaseModel):
    shortfall: Decimal | None = None
    amount: Decimal | None = None
    account_id: int | None = None
    profile_id: int | None = None
    scope: str | None = None


@app.post("/api/liquidity/rescue")
def liquidity_rescue(body: RescueIn, db: Session = Depends(get_db)):
    """Avoid-negative coach: ranked options with estimated costs."""
    from financial_os.services.liquidity_rescue import build_rescue_plan

    return build_rescue_plan(
        db,
        shortfall=body.shortfall,
        amount=body.amount,
        account_id=body.account_id,
        profile_id=body.profile_id,
        scope=body.scope,
    )


@app.get("/api/fees/candidates")
def fees_candidates(
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.fee_scan import scan_fees

    return scan_fees(db, days=days, limit=limit, profile_id=profile_id)


class FeeConfirmIn(BaseModel):
    transaction_id: int
    action: str  # mark_fee | dismiss | recategorize
    category_id: int | None = None


@app.post("/api/fees/confirm")
def fees_confirm(body: FeeConfirmIn, db: Session = Depends(get_db)):
    from financial_os.services.fee_scan import confirm_fee

    try:
        return confirm_fee(
            db,
            transaction_id=body.transaction_id,
            action=body.action,
            category_id=body.category_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/fees/summary")
def fees_summary(
    days: int = Query(365, ge=1, le=730),
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.fee_scan import fee_summary

    return fee_summary(db, days=days, profile_id=profile_id)


@app.post("/api/promo-clock/{account_id}/sink-bill")
def promo_sink_bill(account_id: int, db: Session = Depends(get_db)):
    """Create/update monthly sinking-fund scheduled expense for a 0% promo."""
    from financial_os.services.promo_sink import create_promo_sink_bill

    try:
        return create_promo_sink_bill(db, account_id=account_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/autopay")
def autopay_list(profile_id: Optional[int] = None, db: Session = Depends(get_db)):
    from financial_os.services.autopay import list_autopay

    return list_autopay(db, profile_id=profile_id)


class AutopayIn(BaseModel):
    policy: str  # none | min | statement | promo_sink
    apply_schedule: bool = True


@app.put("/api/autopay/{account_id}")
def autopay_set(account_id: int, body: AutopayIn, db: Session = Depends(get_db)):
    from financial_os.services.autopay import set_autopay

    try:
        return set_autopay(
            db,
            account_id=account_id,
            policy=body.policy,
            apply_schedule=body.apply_schedule,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/intermix/graph")
def intermix_graph(
    days: int = Query(365, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    from financial_os.services.intermix_graph import build_money_map

    return build_money_map(db, days=days)


@app.get("/api/digest/brief")
def digest_brief(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    use_grok: bool = True,
    db: Session = Depends(get_db),
):
    from financial_os.services.digest_brief import build_fiscal_brief

    return build_fiscal_brief(db, profile_id=profile_id, scope=scope, use_grok=use_grok)


@app.get("/api/glance")
def glance(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    mode: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Mobile / multi-client one-shot Spendable + alerts (no fiscal logic in clients)."""
    from financial_os.services.glance import build_glance

    return build_glance(db, profile_id=profile_id, scope=scope, mode=mode)


@app.get("/api/home/simple")
def home_simple(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    mode: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Stupid-simple Home payload: safe to spend, status, do-this-next, wealth tips."""
    from financial_os.services.home_simple import build_home_simple

    return build_home_simple(db, profile_id=profile_id, scope=scope, mode=mode)


@app.get("/api/recurring/suggestions")
def recurring_suggestions(
    profile_id: Optional[int] = None,
    lookback_days: int = Query(180, ge=30, le=400),
    db: Session = Depends(get_db),
):
    """Likely bills/subs from history (confirm before adding)."""
    from financial_os.services.recurring_detect import detect_recurring

    return detect_recurring(db, profile_id=profile_id, lookback_days=lookback_days)


class RecurringAcceptIn(BaseModel):
    name: str
    amount: Decimal  # expense magnitude; sign forced negative
    cadence: str = "monthly"
    next_date: date | None = None
    profile_id: int | None = None
    account_id: int | None = None


@app.post("/api/recurring/accept")
def recurring_accept(body: RecurringAcceptIn, db: Session = Depends(get_db)):
    """One-tap: add detected pattern as scheduled bill."""
    from financial_os.services.recurring_detect import accept_recurring_suggestion

    try:
        return accept_recurring_suggestion(
            db,
            name=body.name,
            amount=body.amount,
            cadence=body.cadence,
            next_date=body.next_date,
            profile_id=body.profile_id,
            account_id=body.account_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/reports/cashflow")
def reports_cashflow(
    days: int = Query(30, ge=7, le=366),
    db: Session = Depends(get_db),
):
    """Cash flow by entity (dream H2-A)."""
    from financial_os.services.reports import cashflow_by_entity

    return cashflow_by_entity(db, days=days)


@app.get("/api/reports/debt")
def reports_debt(
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.reports import debt_snapshot

    return debt_snapshot(db, profile_id=profile_id)


@app.get("/api/tax/year-checklist")
def tax_year_checklist(
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Annual tax handoff prep (not e-file)."""
    from financial_os.services.tax_year import build_tax_year_checklist

    return build_tax_year_checklist(db, year=year)


@app.get("/api/scenarios")
def scenarios_list(
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.scenarios import list_scenarios

    return list_scenarios(db, profile_id=profile_id)


class ScenarioIn(BaseModel):
    name: str
    extra_outflows: list[dict] = []
    profile_id: int | None = None
    scope: str = "entity"
    notes: str | None = None


@app.post("/api/scenarios")
def scenarios_create(body: ScenarioIn, db: Session = Depends(get_db)):
    from financial_os.services.scenarios import create_scenario

    try:
        return create_scenario(
            db,
            name=body.name,
            extra_outflows=body.extra_outflows,
            profile_id=body.profile_id,
            scope=body.scope,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/scenarios/{scenario_id}/run")
def scenarios_run(scenario_id: int, db: Session = Depends(get_db)):
    from financial_os.services.scenarios import run_scenario

    try:
        return run_scenario(db, scenario_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/scenarios/{scenario_id}")
def scenarios_delete(scenario_id: int, db: Session = Depends(get_db)):
    from financial_os.services.scenarios import delete_scenario

    try:
        return delete_scenario(db, scenario_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class ScenarioQuickIn(BaseModel):
    name: str = "What-if purchase"
    amount: Decimal
    on_date: date | None = None
    profile_id: int | None = None
    scope: str = "entity"


@app.post("/api/scenarios/quick")
def scenarios_quick(body: ScenarioQuickIn, db: Session = Depends(get_db)):
    """Save + run a one-shot outflow scenario."""
    from financial_os.services.scenarios import quick_scenario_from_amount

    try:
        return quick_scenario_from_amount(
            db,
            name=body.name,
            amount=body.amount,
            on_date=body.on_date,
            profile_id=body.profile_id,
            scope=body.scope,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/home/month-close")
def home_month_close(
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Month-close checklist (fees · charges · promo · tax · reconcile · backup)."""
    from financial_os.services.month_close import build_month_close

    return build_month_close(db, profile_id=profile_id)


class MonthCloseCompleteIn(BaseModel):
    force: bool = False
    profile_id: int | None = None


@app.post("/api/home/month-close/complete")
def home_month_close_complete(
    body: MonthCloseCompleteIn | None = None,
    db: Session = Depends(get_db),
):
    """Mark current calendar month closed after required steps (or force)."""
    from financial_os.services.month_close import mark_month_closed

    body = body or MonthCloseCompleteIn()
    result = mark_month_closed(
        db,
        force=body.force,
        profile_id=body.profile_id,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "Cannot close month yet")
    return result


@app.get("/api/payments/candidates")
def payment_candidates(
    days: int = Query(14, ge=1, le=60),
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.payment_match import find_payment_candidates

    return find_payment_candidates(db, days=days, profile_id=profile_id)


class PaymentConfirmIn(BaseModel):
    cash_txn_id: int
    card_txn_id: int


@app.post("/api/payments/confirm")
def payment_confirm(body: PaymentConfirmIn, db: Session = Depends(get_db)):
    from financial_os.services.payment_match import apply_payment_as_transfer

    try:
        return apply_payment_as_transfer(
            db, cash_txn_id=body.cash_txn_id, card_txn_id=body.card_txn_id
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/promo-clock")
def promo_clock(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.promo_clock import promo_death_clock

    return promo_death_clock(db, profile_id=profile_id, scope=scope)


class IntermixIn(BaseModel):
    kind: str  # reimburse | distribution | capital_inject | owner_draw | child_allowance
    amount: Decimal
    from_account_id: int
    to_account_id: int
    txn_date: date | None = None
    memo: str | None = None


@app.post("/api/intermix")
def intermix(body: IntermixIn, db: Session = Depends(get_db)):
    from financial_os.services.intermix import apply_intermix

    try:
        return apply_intermix(
            db,
            kind=body.kind,  # type: ignore[arg-type]
            amount=body.amount,
            from_account_id=body.from_account_id,
            to_account_id=body.to_account_id,
            txn_date=body.txn_date,
            memo=body.memo,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/digest")
def digest(
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.digest import build_digest

    return build_digest(db, profile_id=profile_id, scope=scope)


@app.get("/api/permissions/roles")
def permission_roles():
    from financial_os.services.permissions import list_roles

    return {"roles": list_roles(), "default": "owner"}


@app.get("/api/permissions/me")
def permission_me(request: Request, db: Session = Depends(get_db)):
    from financial_os.services.permissions import CAPS, auth_status

    ctx = getattr(request.state, "access", None)
    if ctx is None:
        from financial_os.services.permissions import default_context

        ctx = default_context()
    status = auth_status(db)
    return {
        "user_id": ctx.user_id,
        "username": ctx.username,
        "display_name": ctx.display_name,
        "role": ctx.role.value,
        "authenticated": ctx.authenticated,
        "capabilities": sorted(CAPS.get(ctx.role, set())),
        **status,
    }


@app.get("/api/permissions/auth-status")
def permission_auth_status(db: Session = Depends(get_db)):
    from financial_os.services.permissions import auth_status

    return auth_status(db)


@app.get("/api/permissions/audit")
def permission_audit(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    from financial_os.services.audit import list_events

    return {"events": list_events(db, limit=limit)}


@app.get("/api/permissions/users")
def permission_users(db: Session = Depends(get_db)):
    from financial_os.db import AppUser
    from financial_os.services.permissions import auth_status

    rows = db.query(AppUser).order_by(AppUser.id).all()
    return {
        "users": [
            {
                "id": r.id,
                "username": r.username,
                "display_name": r.display_name,
                "role": r.role,
                "active": r.active,
                "has_token": bool(r.api_token),
            }
            for r in rows
        ],
        **auth_status(db),
    }


class UserIn(BaseModel):
    username: str
    display_name: str
    role: str = "viewer"
    issue_token: bool = True


@app.post("/api/permissions/users")
def create_user(body: UserIn, db: Session = Depends(get_db)):
    from financial_os.db import AppUser
    from financial_os.services.permissions import Role, generate_api_token

    if body.role not in {r.value for r in Role}:
        raise HTTPException(400, f"Invalid role: {body.role}")
    if db.query(AppUser).filter(AppUser.username == body.username).first():
        raise HTTPException(400, "Username exists")
    token = generate_api_token() if body.issue_token else None
    row = AppUser(
        username=body.username.strip(),
        display_name=body.display_name.strip(),
        role=body.role,
        active=True,
        api_token=token,
    )
    db.add(row)
    db.flush()
    from financial_os.services.permissions import auth_status, generate_api_token as _gen

    # When multi-user flips on, ensure the default owner also has a token to continue.
    owner_token = None
    status = auth_status(db)
    if status["multi_user_mode"]:
        owner = db.query(AppUser).filter(AppUser.username == "owner").first()
        if owner and not owner.api_token:
            owner_token = _gen()
            owner.api_token = owner_token
            db.flush()
    status = auth_status(db)
    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "api_token": token,
        "owner_api_token": owner_token,
        "hint": (
            "Store this token — send as X-API-Key on API requests. Shown once. "
            + (
                "Multi-user mode is ON: all clients must use an API key."
                if status["multi_user_mode"]
                else ""
            )
            + (
                " Owner token was minted (owner_api_token) because owner had none."
                if owner_token
                else ""
            )
        ),
        **status,
    }


@app.post("/api/permissions/users/{user_id}/rotate-token")
def rotate_token(user_id: int, db: Session = Depends(get_db)):
    from financial_os.db import AppUser
    from financial_os.services.permissions import generate_api_token

    row = db.get(AppUser, user_id)
    if not row:
        raise HTTPException(404, "User not found")
    token = generate_api_token()
    row.api_token = token
    db.flush()
    return {"id": row.id, "username": row.username, "api_token": token}


@app.get("/api/payoff/{account_id}")
def get_payoff(account_id: int, db: Session = Depends(get_db)):
    a = db.get(Account, account_id)
    if not a or a.kind != "credit":
        raise HTTPException(404, "Credit account not found")
    card = CardView(
        id=a.id,
        name=a.nickname,
        balance=Decimal(a.current_balance or 0),
        credit_limit=Decimal(a.credit_limit or 0),
        available_credit=Decimal(a.available_credit or 0),
        statement_close_day=a.statement_close_day,
        payment_due_day=a.payment_due_day,
        apr=Decimal(a.apr) if a.apr is not None else None,
        promo_apr=Decimal(a.promo_apr) if a.promo_apr is not None else None,
        promo_end_date=a.promo_end_date,
        promo_balance=Decimal(a.promo_balance) if a.promo_balance is not None else None,
        min_payment=Decimal(a.min_payment) if a.min_payment is not None else None,
    )
    return plan_to_dict(plan_card_payoff(card))


# --- Categorizer / rules ---


class RuleIn(BaseModel):
    profile_id: int | None = None
    match_type: str = "contains"
    pattern: str
    category_id: int
    priority: int = 100
    is_transfer: bool = False
    active: bool = True
    notes: str | None = None


class RuleOut(RuleIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str = "user"
    category_name: str | None = None


class CategorizeRequest(BaseModel):
    profile_id: int | None = None
    limit: int = 100
    apply: bool = False
    use_grok: bool = True
    min_confidence: float | None = None


@app.get("/api/categorizer/status")
def categorizer_status():
    return {
        "grok_enabled": app_settings.grok_enabled,
        "model": app_settings.xai_model if app_settings.grok_enabled else None,
        "auto_apply_min_confidence": app_settings.auto_apply_min_confidence,
        "hint": "Optional BYOK: set your FOS_XAI_API_KEY for Grok. Rules work offline forever (free).",
    }


# --- Debt strategy + educational credit health ---


class DebtPlanRequest(BaseModel):
    strategy: str | None = None  # avalanche|snowball|promo_guard|utilization|custom
    extra_monthly: Decimal = Decimal("0")
    save_preference: bool = False
    opportunity_cost_aware: bool | None = None


@app.get("/api/credit/status")
def credit_api_status():
    return {
        "bureau_api": False,
        "credit_karma_api": False,
        "message": (
            "Credit Karma and Equifax/Experian/TransUnion do not offer consumer APIs "
            "for third-party apps. HonestSpend scores and debt plans use your in-app debts "
            "(APR, promo, limits, balances) plus history you enter."
        ),
        "model": "Educational VantageScore 3.0-style factor weights (not official)",
    }


@app.get("/api/credit/health")
def credit_health(db: Session = Depends(get_db)):
    from financial_os.services.debt_service import run_credit_health

    return run_credit_health(db)


class CreditProfileIn(BaseModel):
    """User-entered history for educational score (not bureau data)."""

    credit_on_time_rate: Decimal | None = None  # 0–1
    credit_late_30: int | None = None
    credit_late_60: int | None = None
    credit_late_90: int | None = None
    credit_hard_inquiries: int | None = None
    credit_new_accounts: int | None = None
    credit_reported_vantage: int | None = None  # optional self-reported anchor


@app.get("/api/credit/profile")
def credit_profile_get(db: Session = Depends(get_db)):
    from financial_os.services.debt_service import get_credit_profile

    row = db.get(AppSettings, 1) or AppSettings(id=1)
    p = get_credit_profile(db)
    return {
        "credit_on_time_rate": str(p.on_time_rate),
        "credit_late_30": p.late_30_12m,
        "credit_late_60": p.late_60_12m,
        "credit_late_90": p.late_90_plus_12m,
        "credit_hard_inquiries": p.hard_inquiries_12m,
        "credit_new_accounts": p.new_accounts_12m,
        "credit_reported_vantage": p.reported_vantage,
        "disclaimer": (
            "These inputs feed an educational VantageScore-style model only. "
            "They are not pulled from Credit Karma or any bureau."
        ),
        "source": "app_settings",
        "updated_hint": "PUT /api/credit/profile or Settings",
        "defaults_note": "Perfect on-time history assumed until you edit.",
        "settings_id": getattr(row, "id", 1),
    }


@app.put("/api/credit/profile")
def credit_profile_put(body: CreditProfileIn, db: Session = Depends(get_db)):
    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        db.add(row)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.flush()
    db.refresh(row)
    from financial_os.services.debt_service import get_credit_profile, run_credit_health

    p = get_credit_profile(db)
    health = run_credit_health(db)
    return {
        "ok": True,
        "profile": {
            "credit_on_time_rate": str(p.on_time_rate),
            "credit_late_30": p.late_30_12m,
            "credit_late_60": p.late_60_12m,
            "credit_late_90": p.late_90_plus_12m,
            "credit_hard_inquiries": p.hard_inquiries_12m,
            "credit_new_accounts": p.new_accounts_12m,
            "credit_reported_vantage": p.reported_vantage,
        },
        "score": health.get("score"),
        "band": health.get("band"),
    }


@app.post("/api/debt/plan")
def debt_plan(body: DebtPlanRequest, db: Session = Depends(get_db)):
    from financial_os.services.debt_service import run_debt_plan

    if body.save_preference:
        row = db.get(AppSettings, 1)
        if not row:
            row = AppSettings(id=1)
            db.add(row)
        if body.strategy:
            row.debt_strategy = body.strategy
        row.debt_extra_monthly = body.extra_monthly
        if body.opportunity_cost_aware is not None:
            row.opportunity_cost_aware = body.opportunity_cost_aware
        db.flush()
    return run_debt_plan(
        db,
        strategy=body.strategy,
        extra_monthly=body.extra_monthly,
        opportunity_cost_aware=body.opportunity_cost_aware,
    )


@app.get("/api/debt/plan")
def debt_plan_get(
    strategy: Optional[str] = None,
    extra_monthly: Decimal = Decimal("0"),
    db: Session = Depends(get_db),
):
    from financial_os.services.debt_service import run_debt_plan

    return run_debt_plan(db, strategy=strategy, extra_monthly=extra_monthly)


@app.get("/api/debt/compare")
def debt_compare(
    extra_monthly: Decimal = Decimal("0"),
    db: Session = Depends(get_db),
):
    from financial_os.services.debt_service import run_strategy_compare

    return run_strategy_compare(db, extra_monthly=extra_monthly)


@app.get("/api/rules", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)):
    rows = db.query(CategoryRule).order_by(CategoryRule.priority.desc()).all()
    out = []
    for r in rows:
        cat = db.get(Category, r.category_id)
        out.append(
            {
                **{c.name: getattr(r, c.name) for c in CategoryRule.__table__.columns},
                "category_name": cat.display_name if cat else None,
            }
        )
    return out


class RuleTestIn(BaseModel):
    match_type: str = "contains"
    pattern: str
    limit: int = 80


@app.post("/api/rules/test")
def test_rule_pattern(body: RuleTestIn, db: Session = Depends(get_db)):
    """Preview which recent payees a draft rule would match (no write)."""
    from financial_os.services.categorizer import match_rule

    pat = (body.pattern or "").strip()
    if not pat:
        return {"matches": [], "scanned": 0, "pattern": "", "match_type": body.match_type}

    mt = (body.match_type or "contains").strip().lower()
    # In-memory probe — never persisted (only pattern + match_type used)
    probe = CategoryRule(match_type=mt, pattern=pat, category_id=1)
    lim = max(1, min(int(body.limit or 80), 200))
    # Distinct-ish recent payees from last N txns
    txns = (
        db.query(Transaction)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(lim * 3)
        .all()
    )
    seen: set[str] = set()
    matches: list[dict] = []
    scanned = 0
    for t in txns:
        text = f"{t.payee or ''} {t.memo or ''}".strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        scanned += 1
        if match_rule(probe, key):
            matches.append(
                {
                    "payee": t.payee or text,
                    "txn_date": str(t.txn_date) if t.txn_date else None,
                    "amount": str(t.amount) if t.amount is not None else None,
                    "category_id": t.category_id,
                }
            )
        if scanned >= lim:
            break
        if len(matches) >= 25:
            break
    return {
        "match_type": mt,
        "pattern": pat,
        "scanned": scanned,
        "match_count": len(matches),
        "matches": matches,
    }


@app.post("/api/rules", response_model=RuleOut)
def create_rule(body: RuleIn, db: Session = Depends(get_db)):
    if not db.get(Category, body.category_id):
        raise HTTPException(404, "Category not found")
    row = CategoryRule(**body.model_dump(), source="user")
    db.add(row)
    db.flush()
    db.refresh(row)
    cat = db.get(Category, row.category_id)
    return {**body.model_dump(), "id": row.id, "source": row.source, "category_name": cat.display_name if cat else None}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    row = db.get(CategoryRule, rule_id)
    if not row:
        raise HTTPException(404, "Rule not found")
    row.active = False
    return {"ok": True}


@app.post("/api/categorize/batch")
def categorize_batch(body: CategorizeRequest, db: Session = Depends(get_db)):
    return {
        "results": categorize_uncategorized(
            db,
            profile_id=body.profile_id,
            limit=body.limit,
            apply=body.apply,
            use_grok=body.use_grok,
            min_confidence=body.min_confidence,
        ),
        "grok_enabled": app_settings.grok_enabled,
    }


@app.post("/api/categorize/transaction/{txn_id}")
def categorize_one(
    txn_id: int,
    apply: bool = False,
    use_grok: bool = True,
    learn: bool = False,
    db: Session = Depends(get_db),
):
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")
    sug = suggest_category(db, txn, use_grok=use_grok)
    applied = False
    if apply and sug.category_id:
        txn.category_id = sug.category_id
        txn.confidence = Decimal(str(round(sug.confidence, 4)))
        if sug.is_transfer:
            txn.is_transfer = True
        applied = True
        if learn:
            learn_rule_from_correction(db, txn, sug.category_id)
    return {
        "transaction_id": txn.id,
        "suggestion": {
            "category_id": sug.category_id,
            "category_name": sug.category_name,
            "confidence": sug.confidence,
            "source": sug.source,
            "reason": sug.reason,
            "is_transfer": sug.is_transfer,
        },
        "applied": applied,
    }


@app.get("/api/tax/coa-summary")
def coa_summary(db: Session = Depends(get_db)):
    cats = db.query(Category).all()
    by_form: dict[str, int] = {}
    for c in cats:
        by_form[c.tax_form] = by_form.get(c.tax_form, 0) + 1
    return {
        "total_categories": len(cats),
        "by_tax_form": by_form,
        "disclaimer": "Maps organize records for TurboTax/CPA. Not tax advice.",
    }


@app.get("/api/tax/readiness")
def tax_readiness(
    profile_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.tax_packet import tax_packet_readiness

    y = year or date.today().year
    return tax_packet_readiness(db, profile_id=profile_id, year=y)


@app.get("/api/tax/cpa-pack/download")
def tax_cpa_pack_download(
    profile_id: int,
    year: Optional[int] = None,
    issue_token: bool = Query(True),
    db: Session = Depends(get_db),
):
    from financial_os.services.cpa_pack import build_cpa_pack_zip

    y = year or date.today().year
    try:
        data, meta = build_cpa_pack_zip(
            db, profile_id=profile_id, year=y, issue_token=issue_token
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    fname = meta.get("filename") or f"cpa_pack_{y}.zip"
    return StreamingResponse(
        BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/tax/cpa-pack")
def tax_cpa_pack_meta(
    profile_id: int,
    year: Optional[int] = None,
    issue_token: bool = True,
    db: Session = Depends(get_db),
):
    """Build pack metadata (and token) without downloading ZIP — for UI confirmation."""
    from financial_os.services.cpa_pack import build_cpa_pack_zip

    y = year or date.today().year
    try:
        _data, meta = build_cpa_pack_zip(
            db, profile_id=profile_id, year=y, issue_token=issue_token
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    # Do not re-issue token on download path if we already created — return meta with token once
    return meta


@app.get("/api/tax/packet")
def tax_packet_json(
    profile_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    y = year or date.today().year
    try:
        return build_tax_packet(db, profile_id=profile_id, year=y)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/tax/packet/download")
def tax_packet_download(
    profile_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    y = year or date.today().year
    try:
        packet = build_tax_packet(db, profile_id=profile_id, year=y)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    files = packet_to_csv_files(packet)
    slug = files.pop("_meta_slug")
    year_s = files.pop("_meta_year")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="tax_packet_{slug}_{year_s}.zip"'
        },
    )


@app.post("/api/tax/packet/write")
def tax_packet_write(
    profile_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    y = year or date.today().year
    out = settings.data_dir / "exports"
    try:
        path = write_tax_packet_dir(db, profile_id, y, out)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True, "path": str(path)}


@app.post("/api/import/budget-xlsx")
def import_xlsx_path(body: ImportPathIn, db: Session = Depends(get_db)):
    from financial_os.services.paths_safe import resolve_under_data_dir

    try:
        safe_path = resolve_under_data_dir(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    result = import_budget_xlsx(
        db,
        safe_path,
        profile_slug=body.profile_slug,
        sheet_name=body.sheet_name,
        since=body.since,
        dry_run=body.dry_run,
    )
    return {
        "rows_scanned": result.rows_scanned,
        "transactions_created": result.transactions_created,
        "skipped_empty": result.skipped_empty,
        "skipped_existing": result.skipped_existing,
        "errors": result.errors,
        "date_from": result.date_from.isoformat() if result.date_from else None,
        "date_to": result.date_to.isoformat() if result.date_to else None,
        "dry_run": body.dry_run,
    }


@app.post("/api/import/budget-xlsx/upload")
async def import_xlsx_upload(
    file: UploadFile = File(...),
    profile_slug: str = "personal",
    since: Optional[date] = None,
    dry_run: bool = False,
    db: Session = Depends(get_db),
):
    from financial_os.services.paths_safe import enforce_upload_size, safe_filename

    dest = settings.data_dir / "imports"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / safe_filename(file.filename, default="budget.xlsx")
    content = await file.read()
    try:
        enforce_upload_size(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    target.write_bytes(content)
    result = import_budget_xlsx(
        db,
        target,
        profile_slug=profile_slug,
        since=since,
        dry_run=dry_run,
    )
    return {
        "saved_to": str(target),
        "rows_scanned": result.rows_scanned,
        "transactions_created": result.transactions_created,
        "skipped_empty": result.skipped_empty,
        "skipped_existing": result.skipped_existing,
        "errors": result.errors,
        "date_from": result.date_from.isoformat() if result.date_from else None,
        "date_to": result.date_to.isoformat() if result.date_to else None,
        "dry_run": dry_run,
    }


@app.post("/api/import/bank-csv/preview")
async def import_bank_csv_preview(file: UploadFile = File(...)):
    """Detect columns + sample rows without writing transactions."""
    content = await file.read()
    from io import BytesIO

    from financial_os.services.bank_csv import preview_bank_csv
    from financial_os.services.paths_safe import enforce_upload_size

    try:
        enforce_upload_size(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return preview_bank_csv(BytesIO(content))


@app.get("/api/import/bank-guides")
def import_bank_guides():
    """How to download CSV from major banks (no credentials)."""
    from financial_os.services.bank_guides import list_bank_guides

    return list_bank_guides()


@app.get("/api/import/bank-guides/{guide_id}")
def import_bank_guide_one(guide_id: str):
    from financial_os.services.bank_guides import get_bank_guide

    g = get_bank_guide(guide_id)
    if not g:
        raise HTTPException(404, "Guide not found")
    return g


@app.get("/api/import/inbox")
def import_inbox_status():
    from financial_os.services.import_inbox import ensure_inbox_layout, list_inbox_files

    layout = ensure_inbox_layout()
    return {
        **layout,
        "files": list_inbox_files(),
    }


class InboxProcessIn(BaseModel):
    default_account_id: int | None = None
    auto_categorize: bool = True
    amount_sign: str = "bank"
    dry_run: bool = False


@app.post("/api/import/inbox/process")
def import_inbox_process(body: InboxProcessIn, db: Session = Depends(get_db)):
    from financial_os.services.import_inbox import process_inbox

    return process_inbox(
        db,
        default_account_id=body.default_account_id,
        auto_categorize=body.auto_categorize,
        amount_sign=body.amount_sign,
        dry_run=body.dry_run,
    )


@app.post("/api/import/ofx/preview")
async def import_ofx_preview(file: UploadFile = File(...)):
    """Preview OFX/QFX transactions without writing."""
    content = await file.read()
    from financial_os.services.bank_ofx import preview_ofx
    from financial_os.services.paths_safe import enforce_upload_size

    try:
        enforce_upload_size(content)
        return preview_ofx(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/import/ofx")
async def import_ofx_upload(
    account_id: int = Form(...),
    auto_categorize: bool = Form(True),
    amount_sign: str = Form("bank"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    from financial_os.services.bank_ofx import import_ofx
    from financial_os.services.paths_safe import enforce_upload_size, safe_filename

    try:
        enforce_upload_size(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    try:
        result = import_ofx(
            db,
            account_id=account_id,
            file_obj=content,
            filename=safe_filename(file.filename, default="download.ofx"),
            auto_categorize=auto_categorize,
            amount_sign=amount_sign,
            apply_ledger_balance=True,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {
        "transactions_found": result.transactions_found,
        "transactions_created": result.transactions_created,
        "skipped_existing": result.skipped_existing,
        "skipped_bad": result.skipped_bad,
        "categorized": result.categorized,
        "errors": result.errors,
        "sample": result.sample,
        "account_hint": result.account_hint,
        "bank_id": result.bank_id,
        "ledger_balance": result.ledger_balance,
        "ledger_balance_as_of": result.ledger_balance_as_of,
        "institution_balance_set": result.institution_balance_set,
        "books_balance": result.books_balance,
        "drift": result.drift,
        "next_steps": result.next_steps,
    }


@app.post("/api/import/statement-pdf/preview")
async def import_statement_pdf_preview(file: UploadFile = File(...)):
    """Heuristic PDF statement parse preview (text PDFs only)."""
    content = await file.read()
    from financial_os.services.paths_safe import enforce_upload_size
    from financial_os.services.statement_pdf import preview_statement_pdf

    try:
        enforce_upload_size(content)
        return preview_statement_pdf(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/import/statement-pdf")
async def import_statement_pdf_upload(
    account_id: int = Form(...),
    auto_categorize: bool = Form(True),
    amount_sign: str = Form("bank"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    from financial_os.services.paths_safe import enforce_upload_size, safe_filename
    from financial_os.services.statement_pdf import import_statement_pdf

    try:
        enforce_upload_size(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    try:
        result = import_statement_pdf(
            db,
            account_id=account_id,
            file_obj=content,
            filename=safe_filename(file.filename, default="statement.pdf"),
            auto_categorize=auto_categorize,
            amount_sign=amount_sign,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {
        "pages": result.pages,
        "lines_scanned": result.lines_scanned,
        "transactions_created": result.transactions_created,
        "skipped_existing": result.skipped_existing,
        "skipped_bad": result.skipped_bad,
        "categorized": result.categorized,
        "errors": result.errors,
        "sample": result.sample,
        "raw_text_chars": result.raw_text_chars,
        "next_steps": result.next_steps,
        "ending_balance": result.ending_balance,
        "institution_balance_set": result.institution_balance_set,
        "books_balance": result.books_balance,
        "drift": result.drift,
        "balance_source": result.balance_source,
    }


@app.get("/api/reconcile")
def reconcile_get(
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.reconcile import reconcile_report

    return reconcile_report(db, profile_id=profile_id)


class ReconcileInstitutionIn(BaseModel):
    balance: Decimal
    mark_reconciled: bool = False


class ReconcileTrustIn(BaseModel):
    trust: str  # books | institution


@app.post("/api/reconcile/{account_id}/institution-balance")
def reconcile_set_institution(
    account_id: int,
    body: ReconcileInstitutionIn,
    db: Session = Depends(get_db),
):
    from financial_os.services.reconcile import set_institution_balance

    try:
        return set_institution_balance(
            db, account_id, body.balance, mark_reconciled=body.mark_reconciled
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/reconcile/{account_id}/trust")
def reconcile_trust(account_id: int, body: ReconcileTrustIn, db: Session = Depends(get_db)):
    from financial_os.services.reconcile import trust_balance

    if body.trust not in ("books", "institution"):
        raise HTTPException(400, "trust must be books or institution")
    try:
        return trust_balance(db, account_id, trust=body.trust)  # type: ignore[arg-type]
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/import/bank-csv")
async def import_bank_csv_upload(
    file: UploadFile = File(...),
    account_id: int = Query(...),
    amount_sign: str = Query("bank"),
    auto_categorize: bool = Query(True),
    institution_balance: Optional[Decimal] = Query(None),
    apply_ending_balance: bool = Query(True),
    db: Session = Depends(get_db),
):
    content = await file.read()
    from io import BytesIO

    from financial_os.services.paths_safe import enforce_upload_size, safe_filename

    try:
        enforce_upload_size(content)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    result = import_bank_csv(
        db,
        account_id=account_id,
        file_obj=BytesIO(content),
        filename=safe_filename(file.filename, default="bank.csv"),
        auto_categorize=auto_categorize,
        amount_sign=amount_sign,
        institution_balance=institution_balance,
        apply_ending_balance=apply_ending_balance,
    )
    return {
        "rows_scanned": result.rows_scanned,
        "transactions_created": result.transactions_created,
        "skipped_existing": result.skipped_existing,
        "skipped_bad": result.skipped_bad,
        "categorized": result.categorized,
        "errors": result.errors,
        "next_steps": result.next_steps,
        "ending_balance": result.ending_balance,
        "institution_balance_set": result.institution_balance_set,
        "books_balance": result.books_balance,
        "drift": result.drift,
        "balance_source": result.balance_source,
    }


# --- Plaid ---


class PlaidExchangeIn(BaseModel):
    public_token: str
    profile_id: int
    institution_name: str | None = None
    institution_id: str | None = None


@app.get("/api/plaid/status")
def plaid_status():
    link_url = f"http://{app_settings.host}:{app_settings.port}/static/plaid-link.html"
    return {
        "enabled": app_settings.plaid_enabled,
        "env": app_settings.plaid_env,
        "link_url": link_url,
        "hint": (
            "Optional BYOK: set your own FOS_PLAID_CLIENT_ID and FOS_PLAID_SECRET. "
            "HonestSpend is free — we never bill for bank feeds. CSV/OFX import works offline."
        ),
        "sandbox_hint": "POST /api/plaid/sandbox-link?profile_id=N for one-click sandbox bank (no Link UI).",
        "freeware": True,
        "byok": True,
    }


class ImportSnoozeIn(BaseModel):
    days: int = 7


@app.get("/api/import/reminder")
def import_reminder_status(db: Session = Depends(get_db)):
    from financial_os.services.import_reminders import build_import_reminder

    return build_import_reminder(db)


@app.post("/api/import/reminder/snooze")
def import_reminder_snooze(body: ImportSnoozeIn, db: Session = Depends(get_db)):
    from financial_os.services.import_reminders import snooze_import_reminder

    return snooze_import_reminder(db, days=body.days)


@app.post("/api/import/reminder/ack")
def import_reminder_ack(db: Session = Depends(get_db)):
    """User says they refreshed books (marks last import now without a file)."""
    from financial_os.services.import_reminders import mark_import_activity

    mark_import_activity(db)
    return {"ok": True}


@app.post("/api/plaid/link-token")
def plaid_link_token():
    try:
        data = plaid_service.create_link_token()
        return {"link_token": data.get("link_token"), "expiration": data.get("expiration")}
    except plaid_service.PlaidError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/plaid/exchange")
def plaid_exchange(body: PlaidExchangeIn, db: Session = Depends(get_db)):
    if not db.get(Profile, body.profile_id):
        raise HTTPException(404, "Profile not found")
    try:
        item = plaid_service.exchange_public_token(
            db,
            public_token=body.public_token,
            profile_id=body.profile_id,
            institution_name=body.institution_name,
            institution_id=body.institution_id,
        )
        sync = plaid_service.sync_transactions(db, item)
        return {
            "plaid_item_id": item.id,
            "institution": item.institution_name,
            "sync": sync,
        }
    except plaid_service.PlaidError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/plaid/items")
def plaid_items(db: Session = Depends(get_db)):
    return plaid_service.list_items(db)


@app.post("/api/plaid/sync/{item_pk}")
def plaid_sync(item_pk: int, db: Session = Depends(get_db)):
    item = db.get(PlaidItem, item_pk)
    if not item:
        raise HTTPException(404, "Plaid item not found")
    if (item.status or "").lower() == "disconnected":
        raise HTTPException(400, "Item disconnected — re-link via Plaid Link")
    if not item.access_token:
        raise HTTPException(400, "No access token — re-link required")
    try:
        return plaid_service.sync_transactions(db, item)
    except plaid_service.PlaidError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/plaid/disconnect/{item_pk}")
def plaid_disconnect(
    item_pk: int,
    keep_accounts: bool = Query(True),
    db: Session = Depends(get_db),
):
    try:
        return plaid_service.disconnect_item(db, item_pk, keep_accounts=keep_accounts)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/plaid/sandbox-link")
def plaid_sandbox_link(
    profile_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """One-click sandbox bank for testing (no Link UI)."""
    if not db.get(Profile, profile_id):
        raise HTTPException(404, "Profile not found")
    try:
        public = plaid_service.sandbox_public_token()
        item = plaid_service.exchange_public_token(
            db,
            public_token=public,
            profile_id=profile_id,
            institution_name="First Platypus Bank (Sandbox)",
            institution_id="ins_109508",
        )
        sync = plaid_service.sync_transactions(db, item)
        return {"plaid_item_id": item.id, "sync": sync, "institution": item.institution_name}
    except plaid_service.PlaidError as e:
        raise HTTPException(400, str(e)) from e


# --- Backup / local data (product non-negotiable) ---


class BackupCreateIn(BaseModel):
    as_zip: bool = True
    note: str | None = None


@app.get("/api/system/info")
def system_info():
    from financial_os.services.backup import db_status
    from financial_os.services.paths import data_path_info

    st = db_status()
    paths = data_path_info()
    return {
        "ok": True,
        "version": __version__,
        "product": "HonestSpend",
        "app": settings.app_name,
        "host": settings.host,
        "port": settings.port,
        "grok_enabled": settings.grok_enabled,
        "plaid_enabled": settings.plaid_enabled,
        "data_paths": paths,
        **st,
    }


@app.get("/api/system/paths")
def system_paths():
    from financial_os.services.paths import data_path_info

    return data_path_info()


@app.get("/api/backup/status")
def backup_status(db: Session = Depends(get_db)):
    from financial_os.services.backup import db_status, list_backups, schedule_status

    st = db_status()
    items = [
        {
            "name": b.name,
            "size_bytes": b.size_bytes,
            "created": b.created,
        }
        for b in list_backups()[:20]
    ]
    return {**st, "backups": items, "schedule": schedule_status(db)}


class AutoBackupScheduleIn(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = None
    keep: int | None = None
    run_now: bool = False


@app.get("/api/backup/schedule")
def backup_schedule_get(db: Session = Depends(get_db)):
    from financial_os.services.backup import schedule_status

    return schedule_status(db)


@app.put("/api/backup/schedule")
def backup_schedule_put(body: AutoBackupScheduleIn, db: Session = Depends(get_db)):
    from financial_os.services.backup import maybe_auto_backup, schedule_status

    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1)
        db.add(row)
    if body.enabled is not None:
        row.auto_backup_enabled = body.enabled
    if body.interval_hours is not None:
        row.auto_backup_interval_hours = max(1, min(24 * 30, int(body.interval_hours)))
    if body.keep is not None:
        row.auto_backup_keep = max(1, min(100, int(body.keep)))
    db.flush()
    ran = None
    if body.run_now:
        ran = maybe_auto_backup(db, force=True)
    return {"ok": True, "schedule": schedule_status(db), "ran": ran}


@app.get("/api/backup/list")
def backup_list():
    from financial_os.services.backup import list_backups

    return {
        "backups": [
            {"name": b.name, "size_bytes": b.size_bytes, "created": b.created, "path": str(b.path)}
            for b in list_backups()
        ]
    }


@app.post("/api/backup/create")
def backup_create(body: BackupCreateIn | None = None):
    from financial_os.services.backup import create_backup

    body = body or BackupCreateIn()
    try:
        return create_backup(as_zip=body.as_zip, note=body.note)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


class EncryptedBackupIn(BaseModel):
    password: str
    note: str | None = None
    copy_to_remote: bool = True


@app.post("/api/backup/create-encrypted")
def backup_create_encrypted(body: EncryptedBackupIn):
    from financial_os.services.encrypted_backup import create_encrypted_backup

    try:
        return create_encrypted_backup(
            password=body.password,
            note=body.note,
            copy_to_remote=body.copy_to_remote,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/backup/encrypted")
def backup_list_encrypted():
    from financial_os.services.encrypted_backup import list_encrypted

    return {"items": list_encrypted()}


class DecryptBackupIn(BaseModel):
    path: str | None = None
    name: str | None = None  # under backups/
    password: str


@app.post("/api/backup/decrypt")
def backup_decrypt(body: DecryptBackupIn):
    from financial_os.services.backup import backups_dir
    from financial_os.services.encrypted_backup import decrypt_file_to_backup
    from financial_os.services.paths_safe import resolve_under_data_dir, safe_filename

    if body.path:
        try:
            p = str(resolve_under_data_dir(body.path))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    elif body.name:
        safe = safe_filename(body.name, default="backup.zip")
        p = str(backups_dir() / safe)
    else:
        raise HTTPException(400, "path or name required")
    try:
        return decrypt_file_to_backup(path=p, password=body.password)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class RemoteBackupConfigIn(BaseModel):
    destination_folder: str | None = None
    auto_copy_encrypted: bool = False


@app.get("/api/backup/remote-config")
def backup_remote_get():
    from financial_os.services.encrypted_backup import get_remote_config

    return get_remote_config()


@app.put("/api/backup/remote-config")
def backup_remote_put(body: RemoteBackupConfigIn):
    from financial_os.services.encrypted_backup import set_remote_config

    return set_remote_config(
        destination_folder=body.destination_folder,
        auto_copy_encrypted=body.auto_copy_encrypted,
    )


@app.get("/api/backup/download/{name}")
def backup_download(name: str):
    from financial_os.services.backup import read_backup_bytes

    try:
        data, media, fname = read_backup_bytes(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return StreamingResponse(
        BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/backup/download-live")
def backup_download_live():
    """Stream a zip of the current DB without persisting a named backup."""
    from financial_os.services.backup import zip_stream_from_live_db

    try:
        buf = zip_stream_from_live_db()
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="ledger_live_{stamp}.zip"'},
    )


@app.post("/api/backup/restore/{name}")
def backup_restore(name: str):
    from financial_os.services.backup import restore_from_backup

    try:
        return restore_from_backup(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/backup/restore-upload")
async def backup_restore_upload(file: UploadFile = File(...)):
    from financial_os.services.backup import restore_from_upload
    from financial_os.services.paths_safe import enforce_upload_size, safe_filename

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        enforce_upload_size(content)
        return restore_from_upload(content, safe_filename(file.filename, default="upload.db"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# Static UI
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        return {"message": "API up. UI missing.", "docs": "/docs", "glance": "/glance"}
    return FileResponse(index_path)


@app.get("/glance")
def glance_page():
    """Mobile / Mac / Linux shell — polls /api/glance (no fiscal logic in browser)."""
    path = WEB_DIR / "glance.html"
    if not path.exists():
        return {"message": "glance.html missing", "api": "/api/glance"}
    return FileResponse(path)
