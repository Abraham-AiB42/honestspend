# AP Agency Steals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Productize the *portable* ideas from `AP Agency.xlsm` for HonestSpend multi-entity users: versioned recurring bills, clear pay-from (cash vs card), owner draws, thin payroll-day employer tax, fixed/variable opex, income source tags, and entity year P&L — without Farmers e-Folio, full HR/payroll, or day-grids.

**Architecture:** Extend `ScheduledItem` as the bill/income register (start effective date, series id for amount steps, vendor, opex class, income source). Expanders/Coming up/IFPP already honor `end_date` and account_id; fill the gaps in schema + activation + UI. Payroll-day is a **template** that creates linked expense schedules (net + employer tax), not a payroll engine. Reports roll ledger by year/entity.

**Tech Stack:** Python FastAPI/SQLAlchemy, pytest, WinUI 3 C#

**Source reference:** `C:\Users\AbrahamPeters\OneDrive - AP Agency\AP Agency\AP Agency.xlsm`  
Sheets: Balance · Payroll · Employees · e-Folio · Expenses · **Recurring Expenses** · Debt · EOY Totals

## Global Constraints

- Product for **everyone** with a business entity; no Farmers-specific e-Folio matrix, no state tax table product, no day-grid primary UX.
- Local-first freeware; multi-entity silo default (Business Who).
- Cash honesty: schedules on **credit** accounts do not drain Safe until **Card payment · …** on cash (existing IFPP rule).
- Copy: Safe to spend, pay from, owner draw — never IFPP in UI.
- TDD; schema via `migrations.py` next integer (currently **21** → start at **22**).
- Prefer extend `ScheduledItem` over parallel “recurring tables.”
- Coming up / cash runway / mark-paid must keep working after schema changes.

## Explicit non-goals

- Full payroll (W-2, commissions matrix, Paychex import)
- Employee roster product (start/term as first-class HR)
- Carrier e-Folio NB/Service line catalog
- Excel day-grid Balance/Expenses UI
- Personal Budget.xlsx features already shipped

---

## Steal map (locked)

| # | AP Agency idea | HonestSpend deliverable | Priority |
|---|----------------|-------------------------|----------|
| 1 | Recurring start/end + amount steps (rent) | Series + effective windows + step helper | P0 |
| 2 | Account column (Chase/Amex) | Pay-from UX + `vendor` field | P0 |
| 3 | Owner column separate from expenses | `kind=owner_draw` (+ income already exists) | P1 |
| 4 | Payroll + employer tax on pay date | Payroll package template → linked schedules | P1 |
| 5 | Fixed vs variable expense columns | `opex_class` fixed\|variable + budget filter | P2 |
| 6 | e-Folio as income by source | `income_source` on income schedules | P2 |
| 7 | EOY Totals multi-year P&L | Entity year P&L report API + Full books page | P2 |

---

## File map

| Path | Responsibility |
|------|----------------|
| `src/honestspend/db.py` | ScheduledItem columns; optional Category flags |
| `src/honestspend/migrations.py` | SCHEMA 22+ |
| `src/honestspend/services/schedule_series.py` | **New** — create step, activate series as-of, list series |
| `src/honestspend/services/payroll_package.py` | **New** — create net + employer-tax schedules |
| `src/honestspend/services/entity_pnl.py` | **New** — year P&L from ledger |
| `src/honestspend/engine/schedule_expand.py` | Honor `start_date` / effective window if added |
| `src/honestspend/services/coming_up.py` | Treat owner_draw as outflow; no credit skip change |
| `src/honestspend/api/app.py` | CRUD fields + series + payroll package + P&L |
| `clients/.../Pages/*` | Bills/schedules UI, business setup, reports |
| `docs/BUSINESS_ENTITY.md` | **New** — product contract for steals |
| `tests/test_schedule_series.py` | Versioned bills |
| `tests/test_payroll_package.py` | Employer tax pair |
| `tests/test_entity_pnl.py` | Year rollup |

---

## Domain model (ScheduledItem extensions)

### New / clarified fields

| Field | Type | Meaning |
|-------|------|---------|
| `start_date` | Date nullable | First date this row may fire (null = from forever / next_date only) |
| `series_id` | str(36) nullable | UUID shared by stepped versions of the same bill (e.g. Rent) |
| `series_label` | str(128) nullable | Display name of series (“Office rent”) |
| `vendor` | str(128) nullable | Payee/vendor (AP Agency “Vendor”) |
| `opex_class` | str(16) nullable | `fixed` \| `variable` \| null (personal) |
| `income_source` | str(64) nullable | e.g. `farmers_efolio`, `other` — income only |
| `kind` | existing | Add **`owner_draw`** alongside `expense` \| `income` |

**Active window for expand:**  
Occurrence date `d` is included only if:

```
(active)
and (start_date is null or d >= start_date)
and (end_date is null or d <= end_date)
```

Today expand only uses `end_date` on the hard horizon; **Task 2** wires `start_date` into `expand_scheduled` (optional param or read from item in callers).

### Series rules (rent steps)

Example AP Agency rent:

| series_label | amount | start | end |
|--------------|--------|-------|-----|
| Office rent | -2000 | 2026-04-16 | 2026-07-28 |
| Office rent | -2100 | 2026-07-29 | 2027-07-30 |
| Office rent | -2200 | 2027-07-29 | null |

Same `series_id`. Only **one** row should be `active=True` at a time for a given day, **or** expand filters by window so only the matching segment fires (prefer **window filter**, keep all rows active=True if non-overlapping windows).

**Prefer non-overlapping windows + all active=True** so history stays visible.

### Annual lumps

Cadence stays `yearly` or monthly with short start/end (BOP Aug 2–5). No special type — document pattern: `start_date`/`end_date` within same month + yearly cadence, or one-shot: `cadence=monthly`, end_date = start month.

---

### Task 1: Schema — schedule window + series + vendor + opex + income_source + owner_draw

**Files:**
- Modify: `src/honestspend/db.py` (`ScheduledItem`)
- Modify: `src/honestspend/migrations.py` (`SCHEMA_VERSION` 21→22, `_mig_22_schedule_agency_steals`)
- Test: `tests/test_schedule_schema_agency.py`

**Migration SQL (SQLite ALTER pattern):**

```sql
ALTER TABLE scheduled_items ADD COLUMN start_date DATE;
ALTER TABLE scheduled_items ADD COLUMN series_id VARCHAR(36);
ALTER TABLE scheduled_items ADD COLUMN series_label VARCHAR(128);
ALTER TABLE scheduled_items ADD COLUMN vendor VARCHAR(128);
ALTER TABLE scheduled_items ADD COLUMN opex_class VARCHAR(16);
ALTER TABLE scheduled_items ADD COLUMN income_source VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_scheduled_items_series ON scheduled_items(series_id);
```

- [ ] **Step 1: Failing test** — after migrate, columns exist; create item with `kind=owner_draw`, `series_id`, `start_date`

- [ ] **Step 2: Implement model + migration**

- [ ] **Step 3: Pass + commit** `feat(schema): schedule series, vendor, opex, owner_draw`

---

### Task 2: Expand respects start_date; kind owner_draw is cash outflow

**Files:**
- Modify: `src/honestspend/engine/schedule_expand.py`
- Modify: `src/honestspend/services/coming_up.py` (owner_draw → out)
- Modify: `src/honestspend/services/ifpp_service.py` if kind filters exist
- Test: `tests/test_schedule_expand_window.py`, extend `tests/test_coming_up.py`

**Interfaces:**

```python
def expand_scheduled(
    *,
    item_id: int,
    name: str,
    amount: Decimal,
    next_date: date,
    cadence: str,
    certainty: str,
    as_of: date,
    horizon_end: date,
    end_date: date | None = None,
    start_date: date | None = None,  # NEW
    active: bool = True,
) -> list[ScheduledView]:
    ...
    # Skip occurrence if start_date and cursor < start_date
    # Skip if end_date and cursor > end_date (existing hard_end)
```

All callers that expand items must pass `start_date=getattr(s, "start_date", None)`:
- `ifpp_service.py`
- `coming_up.py`
- `cash_runway.py`
- `schedule_mark_paid` if relevant

**owner_draw:** amount &lt; 0, direction=out, never income for payday detection.

- [ ] **Step 1: Test** occurrence before start_date excluded; after included

- [ ] **Step 2: Wire expand + all call sites**

- [ ] **Step 3: Commit** `feat: schedule start_date window + owner_draw outflow`

---

### Task 3: Series service — create step / list series / supersede

**Files:**
- Create: `src/honestspend/services/schedule_series.py`
- Test: `tests/test_schedule_series.py`

**Interfaces:**

```python
def new_series_id() -> str:
    """uuid4 hex or str"""

def create_schedule_segment(
    session,
    *,
    profile_id: int,
    series_label: str,
    amount: Decimal,  # negative expense
    next_date: date,
    cadence: str = "monthly",
    day_of_month: int | None = None,  # if set, align next_date day
    start_date: date | None = None,
    end_date: date | None = None,
    account_id: int | None = None,
    vendor: str | None = None,
    opex_class: str | None = None,
    series_id: str | None = None,  # None → new series
    kind: str = "expense",
) -> ScheduledItem:
    ...

def add_series_step(
    session,
    series_id: str,
    *,
    new_amount: Decimal,
    effective_from: date,
    end_date: date | None = None,
    ...
) -> ScheduledItem:
    """Close previous open-ended segment (set end_date = effective_from - 1 day)
    and insert new segment with same series_id/label/account/vendor."""

def list_series(session, profile_id: int) -> list[dict]:
    """Group by series_id: label, segments[{id, amount, start, end, active window}]"""
```

- [ ] **Step 1: Test rent step** — two segments, expand mid-first → amount A; mid-second → amount B

- [ ] **Step 2: Implement**

- [ ] **Step 3: Commit** `feat: versioned schedule series (rent steps)`

---

### Task 4: API for schedule fields + series

**Files:**
- Modify: `src/honestspend/api/app.py` (Scheduled create/patch models)
- Test: `tests/test_schedule_api_agency.py`

**Endpoints:**
- Existing scheduled create/update accept new fields
- `POST /api/scheduled/series/step` body: `{series_id, new_amount, effective_from, end_date?}`
- `GET /api/scheduled/series?profile_id=`

```python
class ScheduleSeriesStepIn(BaseModel):
    series_id: str
    new_amount: Decimal
    effective_from: date
    end_date: date | None = None
    next_date: date | None = None  # default effective_from
```

- [ ] **Step 1–3: Tests + implement + commit** `feat(api): schedule series and extended fields`

---

### Task 5: WinUI — bill editor pay-from, vendor, steps, owner draw

**Files:**
- Modify: schedule/bill UI (Add hub wizard, Full books bills page if any — search `Scheduled`, `Recurring`)
- Modify: `LedgerApiClient.cs`
- Prefer: Entities or a **Bills** surface under Full books; Simple Add bill wizard fields

**UI requirements:**
- Pay from: account picker (cash **or** credit)
- Vendor text
- Start / end dates
- Kind: Bill · Income · Owner draw
- Opex class (show when profile entity_type=business): Fixed / Variable
- Income source (when kind=income)
- “Change amount on date…” → series step API

Plain language: **Pay from**, **Vendor**, **Starts**, **Ends**, **Owner draw**

- [ ] **Step 1: Wire client DTOs**

- [ ] **Step 2: Forms + series step dialog**

- [ ] **Step 3: Build x64 + commit** `feat(winui): bill series, vendor, pay-from, owner draw`

---

### Task 6: Payroll package (business entity thin)

**Files:**
- Create: `src/honestspend/services/payroll_package.py`
- API + test
- Optional WinUI business setup

**Model (no employee table in v1):**

```python
def create_payroll_package(
    session,
    *,
    profile_id: int,
    name: str,  # "Biweekly payroll"
    pay_date: date,
    cadence: str,  # biweekly|semimonthly|monthly
    net_payroll: Decimal,  # positive magnitude; stored negative
    employer_tax: Decimal,  # positive magnitude; stored negative
    cash_account_id: int,
    series_label: str | None = None,
) -> dict:
    """
    Creates two expense schedules same next_date/cadence/account:
      1) name="{name} · net" amount=-net
      2) name="{name} · employer tax" amount=-tax
    Optional notes link package_id=uuid
    Returns {package_id, net_id, tax_id}
    """
```

Coming up shows both on pay day → Safe honest.

- [ ] **Step 1: Test** package creates two items; coming_up includes both

- [ ] **Step 2: API** `POST /api/payroll-packages`

- [ ] **Step 3: Simple business wizard button** optional “Add payroll day”

- [ ] **Step 4: Commit** `feat: payroll package net + employer tax schedules`

---

### Task 7: Fixed/variable opex + income_source filters

**Files:**
- Budget service / reports: filter or tag
- Categories: optional default opex_class for business categories
- Test filters

**Rules:**
- `opex_class=fixed` expenses eligible for “fixed opex reserve” note in why_this_number (optional soft)
- Income list/API can filter `income_source`

- [ ] **Step 1: Tests** create with opex_class; list filter

- [ ] **Step 2: API query params**

- [ ] **Step 3: Commit** `feat: opex_class and income_source on schedules`

---

### Task 8: Entity year P&L report

**Files:**
- Create: `src/honestspend/services/entity_pnl.py`
- API: `GET /api/reports/entity-pnl?profile_id=&year=`
- WinUI Full books Reports or new section
- Test: seed txns → income/expense/owner_draw buckets

**Buckets (from ledger, not day-grid):**

| Bucket | Rule |
|--------|------|
| income | credits on cash/income categories or positive non-transfer |
| payroll | category or name contains payroll / package net |
| taxes | tax vault outflows + employer tax schedules posted |
| owner_draw | kind owner_draw or category owner |
| expenses | other expenses |
| by_category | top categories |

Keep honest: use existing category_id + kind; don’t invent carrier lines.

```python
def build_entity_pnl(session, profile_id: int, year: int) -> dict:
    return {
      "profile_id", "year",
      "income", "payroll", "taxes", "owner_draws", "expenses",
      "net": income + payroll + taxes + owner_draws + expenses,  # signed
      "by_category": [...],
    }
```

- [ ] **Step 1: Unit test YTD**

- [ ] **Step 2: API + minimal WinUI table**

- [ ] **Step 3: Commit** `feat: entity year P&L report`

---

### Task 9: Docs + Coming up / business contract

**Files:**
- Create: `docs/BUSINESS_ENTITY.md`
- Modify: `docs/SIMPLE_MODE.md` (owner draw / payroll day mention)
- Modify: `CHANGELOG.md`

Document:
- Versioned bills (rent steps)
- Pay from cash vs card
- Owner draw
- Payroll package
- What we deliberately don’t steal (e-Folio matrix, full payroll)

- [ ] **Step 1: Write docs**

- [ ] **Step 2: Commit** `docs: business entity steals from AP Agency patterns`

---

## Acceptance scenarios

| # | Scenario | Expect |
|---|----------|--------|
| A | Rent series $2k then $2.1k from July 29 | July 1 expand → 2000; Aug 1 → 2100 |
| B | Utility on Amex account_id | Not in cash Coming up; Card payment is |
| C | Owner draw monthly | Coming up outflow; not a payday |
| D | Payroll package biweekly | Two lines same date; Safe drops by net+tax |
| E | Entity P&L 2026 | Income/expense totals match seeded txns |
| F | Personal entity | opex_class optional; no forced payroll UI |

---

## Suggested execution order

1 → 2 → 3 → 4 → 5 (P0–P1 core UX)  
6 → 7 → 8 → 9 (business polish)

Tasks 6–8 can partial-parallel after 4 if agents use isolated files.

---

## Self-review

- [x] Spec covers all “worth stealing” rows; non-goals explicit  
- [x] No TBD / implement later without code  
- [x] series_id / start_date / expand contract consistent across tasks  
- [x] IFPP cash-side rule preserved  

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-11-agency-steals.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  

**2. Inline Execution** — this session with checkpoints  

**Which approach?**
