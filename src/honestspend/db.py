from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from honestspend.config import settings


class Base(DeclarativeBase):
    pass


class AccountKind(str, Enum):
    checking = "checking"
    savings = "savings"
    credit = "credit"
    cash = "cash"
    loan = "loan"
    investment = "investment"
    other = "other"


class IfppMode(str, Enum):
    conservative = "conservative"
    expected = "expected"


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    # individual | business | child (legacy: personal maps to individual)
    entity_type: Mapped[str] = mapped_column(String(32))
    tax_form_primary: Mapped[str] = mapped_column(String(16))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Child entities may link to a household/personal parent (optional)
    parent_profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("profiles.id"), nullable=True
    )
    # Tax geo (multi-state lite — notes for CPA, not filing engine)
    home_state: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # e.g. TX, CA
    multi_state: Mapped[bool] = mapped_column(Boolean, default=False)
    filing_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON string: [{"state":"TX","pct":60},{"state":"CA","pct":40}]
    state_allocation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    accounts: Mapped[list[Account]] = relationship(back_populates="profile")
    categories: Mapped[list[Category]] = relationship(back_populates="profile")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="profile")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("profiles.id"), nullable=True)
    # personal | business | system — system has profile_id null and applies globally
    scope: Mapped[str] = mapped_column(String(32), default="system")
    tax_form: Mapped[str] = mapped_column(String(32), default="none")
    tax_line: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sch_c_line: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    deductibility: Mapped[str] = mapped_column(String(32), default="none")
    partial_rule: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    budget_group: Mapped[str] = mapped_column(String(64), default="other")
    other_deduction_detail: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    legacy_excel: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    profile: Mapped[Optional[Profile]] = relationship(back_populates="categories")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    kind: Mapped[str] = mapped_column(String(32))
    nickname: Mapped[str] = mapped_column(String(128))
    institution: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    current_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Last known balance from bank/Plaid (for reconcile drift); books remain current_balance
    institution_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    last_reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Credit: available to charge; loans: remaining principal often stored in balance
    available_credit: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    is_cash_for_ifpp: Mapped[bool] = mapped_column(Boolean, default=False)
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    # Card terms
    statement_close_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payment_due_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    apr: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 4), nullable=True)
    promo_apr: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 4), nullable=True)
    promo_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    promo_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    min_payment: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    rewards_program: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    utilization_warn_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Credit / debt planning
    opened_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority_rank: Mapped[int] = mapped_column(Integer, default=100)  # custom payoff order
    # Yield on cash/savings (APY as decimal, e.g. 0.06 = 6%). Used for opportunity-cost vs debt APR.
    apy: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 5), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    plaid_item_pk: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plaid_items.id"), nullable=True
    )
    plaid_account_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Sole pay-policy authority (what to pay): none | min | statement | promo_sink | fixed | books
    autopay_policy: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Deprecated wizard alias (one release): minimum | fixed | statement | interest_saving | books.
    # Write-through only — amount logic must not read this (use autopay_policy).
    payment_option: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # When policy=fixed, planned payment amount
    payment_fixed_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    # Per-account rainy-day floor (cash accounts); total floor remains AppSettings.safety_buffer
    safety_buffer: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    # Cash/checking that funds card payments (statement cycle / autopay)
    payment_funding_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    # user | import | plaid | default — who last set close/due/funding/policy
    cycle_config_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Cached open-cycle projected statement + next payment under policy
    statement_balance_cached: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    next_payment_amount_cached: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    next_payment_date_cached: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # When cash card payment is scheduled: on_due | on_close | day_before_close
    payment_timing: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # JSON map of category → rewards percent points, e.g. {"gas":5,"general":1}
    rewards_rates_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    profile: Mapped[Profile] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")


class StatementCycle(Base):
    """Credit statement cycle window + projected/actual balances and payment.

    status: open | closed | paid
    source: projected | import | plaid | user
    cycle_end is the statement close date.
    """

    __tablename__ = "statement_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    cycle_start: Mapped[date] = mapped_column(Date)
    cycle_end: Mapped[date] = mapped_column(Date)  # close date
    due_date: Mapped[date] = mapped_column(Date)
    projected_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    actual_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    payment_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    payment_funding_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed|paid
    source: Mapped[str] = mapped_column(
        String(32), default="projected"
    )  # projected|import|plaid|user


class PromoInstallmentLine(Base):
    """ISB-class promo / installment plan carve-out on a credit account.

    principal_remaining + monthly_payment feed statement payment math:
    statement pay = max(0, balance - sum(principal)) + sum(monthly_due).
    source: user | import | statement | isb
    """

    __tablename__ = "promo_installment_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    principal_remaining: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    monthly_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="user")  # user|import|statement|isb


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    txn_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # + inflow, - outflow
    payee: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="cleared")
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    receipt_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_pair_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # null | confirmed_fee | dismissed | recategorized
    fee_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(tz=None)
    )

    profile: Mapped[Profile] = relationship(back_populates="transactions")
    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Optional[Category]] = relationship()


class ScheduledItem(Base):
    """Recurring bill or income.

    account_id = cash/credit account this hits (required for expenses in UI).
    end_date = last date it can fire; null = ongoing. Ending sets active=False and end_date.
    """

    __tablename__ = "scheduled_items"
    __table_args__ = (Index("ix_scheduled_items_series", "series_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # + income, - bill
    next_date: Mapped[date] = mapped_column(Date)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    cadence: Mapped[str] = mapped_column(String(32), default="monthly")  # weekly|biweekly|semimonthly|monthly|yearly
    certainty: Mapped[str] = mapped_column(String(32), default="fixed")  # fixed|expected|historical_avg
    # expense | income | owner_draw — derived from amount sign but stored for clear UI filters
    kind: Mapped[str] = mapped_column(String(16), default="expense")
    series_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    series_label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    vendor: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    opex_class: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # fixed|variable
    income_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class AppUser(Base):
    """Local permission stack user (foundation for multi-client sharing)."""

    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="owner")  # owner|bookkeeper|cpa_viewer|viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Opaque API token for multi-client access (store as plain for local freeware; rotate anytime)
    api_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(tz=None)
    )


class PlaidItem(Base):
    """Linked bank connection (Plaid Item). Access token stays local."""

    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    item_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    access_token: Mapped[str] = mapped_column(String(256))
    institution_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    institution_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    transactions_cursor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(tz=None)
    )


class AuditEvent(Base):
    """Lite multi-user audit trail."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="owner")
    role: Mapped[str] = mapped_column(String(32), default="owner")
    action: Mapped[str] = mapped_column(String(64))
    path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(tz=None)
    )


class ImportPreset(Base):
    """Remember bank CSV mapping / amount sign per institution key."""

    __tablename__ = "import_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    amount_sign: Mapped[str] = mapped_column(String(16), default="bank")  # bank | invert
    mapping_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"), nullable=True)


class WhatIfScenario(Base):
    """Named IFPP what-if (extra outflows JSON) — dream H2-D."""

    __tablename__ = "whatif_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("profiles.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="entity")
    extra_outflows_json: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class BudgetRule(Base):
    """Period budget plan per entity (profile) + category.

    period: daily | weekly | monthly
    active_weekdays: bitmask Mon=bit0 … Sun=bit6 (Python date.weekday())
    """

    __tablename__ = "budget_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    period: Mapped[str] = mapped_column(String(16), default="monthly")  # daily|weekly|monthly
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Mon–Fri default: bits 0..4 = 31
    active_weekdays: Mapped[int] = mapped_column(Integer, default=31)
    week_starts_on: Mapped[int] = mapped_column(Integer, default=0)  # 0=Mon … 6=Sun
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual|suggested|accepted_suggestion
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(tz=None)
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    category: Mapped[Category] = relationship()


class BudgetAdjustment(Base):
    """Temporary cut / skip that frees budget reserve for Safe to spend."""

    __tablename__ = "budget_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    budget_rule_id: Mapped[int] = mapped_column(ForeignKey("budget_rules.id"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    # skip_workdays | scale_remaining | release_remaining
    kind: Mapped[str] = mapped_column(String(32))
    params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    applies_from: Mapped[date] = mapped_column(Date)
    applies_to: Mapped[date] = mapped_column(Date)
    note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(tz=None)
    )


class CategoryRule(Base):
    """Merchant/payee pattern → category. Priority: higher wins first."""

    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("profiles.id"), nullable=True)
    # Match against payee + memo (case-insensitive contains / exact / starts_with)
    match_type: Mapped[str] = mapped_column(String(32), default="contains")
    pattern: Mapped[str] = mapped_column(String(256))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="user")  # user|seed|learned
    notes: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ifpp_mode: Mapped[str] = mapped_column(String(32), default=IfppMode.conservative.value)
    safety_buffer: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("1000"))
    never_negative_scope: Mapped[str] = mapped_column(String(64), default="checking")
    utilization_warn_soft: Mapped[int] = mapped_column(Integer, default=10)
    utilization_warn_hard: Mapped[int] = mapped_column(Integer, default=30)
    horizon_days: Mapped[int] = mapped_column(Integer, default=45)
    auto_categorize_on_import: Mapped[bool] = mapped_column(Boolean, default=True)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    product_name: Mapped[str] = mapped_column(String(64), default="HonestSpend")
    # Debt payoff preference
    debt_strategy: Mapped[str] = mapped_column(String(32), default="avalanche")
    debt_extra_monthly: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Opportunity cost: cash yield hurdle for "pay debt vs keep money earning"
    # null = auto = max APY on cash/savings accounts
    opportunity_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 5), nullable=True)
    opportunity_cost_aware: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional tax adjustment: after-tax opportunity ≈ APY * (1 - tax_rate)
    opportunity_tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    # Credit health inputs (user-supplied — not from bureaus)
    credit_on_time_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1"))
    credit_late_30: Mapped[int] = mapped_column(Integer, default=0)
    credit_late_60: Mapped[int] = mapped_column(Integer, default=0)
    credit_late_90: Mapped[int] = mapped_column(Integer, default=0)
    credit_hard_inquiries: Mapped[int] = mapped_column(Integer, default=0)
    credit_new_accounts: Mapped[int] = mapped_column(Integer, default=0)
    credit_reported_vantage: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Tax vault — reserved cash that reduces Spendable (not invested product)
    tax_vault_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tax_vault_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    # Optional auto-set-aside rate on positive scheduled income (0.25 = 25%)
    tax_vault_income_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    # Variable income cliffs (optional) — haircut expected income in IFPP
    income_cliff_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    income_cliff_factor: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1"))
    # Local auto-backup (product non-negotiable: never lose the books)
    auto_backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_backup_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    auto_backup_keep: Mapped[int] = mapped_column(Integer, default=14)
    auto_backup_last_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # entity = silo Spendable per profile; group = combined (owner multi-entity view)
    ifpp_scope: Mapped[str] = mapped_column(String(16), default="entity")
    # When true, IFPP ignores pending transactions if we ever project from ledger (balances still authoritative)
    ifpp_cleared_only: Mapped[bool] = mapped_column(Boolean, default=True)
    # off | warn | hard — write path never-negative checking enforcement
    never_negative_enforcement: Mapped[str] = mapped_column(String(16), default="warn")
    # Freeware money-in: remind to download CSV/OFX/statements (not bank passwords)
    # off | daily | weekly | monthly
    import_reminder_cadence: Mapped[str] = mapped_column(String(16), default="weekly")
    # transactions | statements | both
    import_reminder_focus: Mapped[str] = mapped_column(String(16), default="transactions")
    # Last successful CSV/OFX/Plaid import (drives due logic)
    import_last_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # User snoozed until this date (inclusive quiet until after)
    import_reminder_snooze_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Month-close ritual (YYYY-MM + timestamp when user marked closed)
    month_close_period: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    month_close_last_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Period budgets: reserve remaining into Safe to spend (Excel Budget.xlsx parity)
    budget_reserve_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    budget_week_starts_on: Mapped[int] = mapped_column(Integer, default=0)  # 0=Mon
    # Mon–Fri default bitmask
    budget_workdays: Mapped[int] = mapped_column(Integer, default=31)
    # Smart setup wizard (resumable multi-phase)
    # welcome | path | plaid_keys | plaid_link | ai_keys | cash_loop | import_cash
    # | discover | liabilities | recurring | categorize | budgets | buffers | manual | done
    setup_phase: Mapped[str] = mapped_column(String(32), default="welcome")
    # plaid | csv | manual | null
    setup_path: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    setup_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Coming up strip window (Simple Home)
    # auto | calendar | paydays
    coming_up_window_mode: Mapped[str] = mapped_column(String(16), default="auto")
    # Allowed 7–14; service clamps
    coming_up_calendar_days: Mapped[int] = mapped_column(Integer, default=14)
    # Allowed 1–2; service clamps
    coming_up_payday_count: Mapped[int] = mapped_column(Integer, default=1)
    coming_up_show_income: Mapped[bool] = mapped_column(Boolean, default=True)


def _set_sqlite_pragma(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str | None = None):
    u = url or settings.sqlalchemy_url
    kwargs: dict = {"future": True}
    if u.startswith("sqlite"):
        # NullPool releases file handles immediately (needed to seal DB on Windows)
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(u, **kwargs)
    if u.startswith("sqlite"):
        event.listen(engine, "connect", _set_sqlite_pragma)
    return engine


def make_session_factory(engine=None):
    eng = engine or make_engine()
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)


def init_db(engine=None) -> None:
    eng = engine or make_engine()
    Base.metadata.create_all(eng)
    from honestspend.migrations import run_migrations

    run_migrations(eng)
    # Apply pending staged restore (safe backup restore from prior process)
    try:
        from honestspend.services.backup import apply_pending_restore

        apply_pending_restore()
    except Exception:
        pass


def get_session() -> Generator[Session, None, None]:
    factory = make_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
