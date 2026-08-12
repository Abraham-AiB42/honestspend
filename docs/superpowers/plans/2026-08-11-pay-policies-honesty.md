# Pay Policies + Honesty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `books` pay policy + close-based pay timing, utilization/interest honesty, and educational credit advice for everyone.

**Architecture:** Extend `statement_cycle` for amount+timing math; `payment_timing` column; honesty helpers; API cycle payload; Credit WinUI controls. Builds on statement-cycles cash schedules.

**Tech Stack:** Python FastAPI/SQLAlchemy, pytest, WinUI 3 C#

## Global Constraints

- Local-first freeware; never-neg on cash payment creates (not schedule upsert).
- Intentional 0% float: `statement` keeps carve-out; `books` pays full books with promo warning.
- Simple mode: no new credit desk; Full books Credit only for controls.
- Copy: Safe to spend, statement balance, next payment — never IFPP in UI; credit advice is educational only.
- TDD; schema bump via migrations next integer (19 if 18 is promo lines).
- No food/gas credit payment pad — budgets own that.

---

## File map

| Path | Responsibility |
|------|----------------|
| `src/honestspend/db.py` | `payment_timing` on Account |
| `src/honestspend/migrations.py` | mig 19 |
| `src/honestspend/services/statement_cycle.py` | books policy, timing → pay date, project fields |
| `src/honestspend/services/card_honesty.py` | **New** util/interest/advice helpers |
| `src/honestspend/services/autopay.py` | POLICIES + books; schedule date from projection |
| `src/honestspend/api/app.py` | cycle-config timing; honesty on GET cycle |
| `clients/.../CreditPage.*` | policy/timing/honesty UI |
| `clients/.../LedgerApiClient.cs` | timing field |
| `docs/STATEMENT_CYCLES.md` | policies + timing + advice |
| `tests/test_pay_timing.py` | timing + books |
| `tests/test_card_honesty.py` | util/interest |
| `tests/test_statement_cycle.py` | books formula |

---

### Task 1: Schema payment_timing

**Files:**
- Modify: `src/honestspend/db.py`
- Modify: `src/honestspend/migrations.py`
- Test: `tests/test_pay_timing_schema.py`

**Interfaces:**
- Produces: `Account.payment_timing` Optional[str] — `on_due` | `on_close` | `day_before_close`

- [ ] **Step 1: Failing test** — hasattr + migrate roundtrip set `day_before_close`

- [ ] **Step 2: Implement column + SCHEMA_VERSION += 1 migration ALTER**

- [ ] **Step 3: Pass + commit** `feat(schema): payment_timing on accounts`

---

### Task 2: Timing + books payment math

**Files:**
- Modify: `src/honestspend/services/statement_cycle.py`
- Test: `tests/test_pay_timing.py`, extend `tests/test_statement_cycle.py`

**Interfaces:**
```python
TIMINGS = frozenset({"on_due", "on_close", "day_before_close"})

def day_before(d: date) -> date: ...

def next_payment_date(
    *,
    as_of: date,
    timing: str | None,
    close_day: int | None,
    due_day: int | None,
) -> date | None:
    """on_due → next_due_date; on_close → next close ≥ as_of; day_before_close → day_before(that close)."""

def compute_next_payment(...):  # add policy books → max(0, bal)
```

`project_card_payment` returns:
- `next_due` = effective pay date from timing
- `payment_timing`
- `warnings`: list[str] if policy books and promo_remaining > 0

- [ ] **Step 1: Tests**
  - close_day=18, as_of=2026-09-15, timing on_close → 2026-09-18
  - day_before_close → 2026-09-17
  - books balance 800 → payment 800 (promo ignored for amount)
  - books + promo_remaining 300 → payment 800, warning non-empty

- [ ] **Step 2: Implement**

- [ ] **Step 3: Commit** `feat: books policy and close-based pay timing`

---

### Task 3: Honesty helpers

**Files:**
- Create: `src/honestspend/services/card_honesty.py`
- Test: `tests/test_card_honesty.py`

**Interfaces:**
```python
def estimated_min_payment(balance, min_payment) -> Decimal: ...
def estimated_monthly_interest(balance, apr) -> Decimal:  # (apr/12)*balance if apr else 0
def utilization_pct(balance, limit) -> Decimal | None: ...
def amount_to_utilization_target(balance, limit, target_pct: int) -> Decimal: ...
def credit_advice_for_policy(policy, timing, *, has_promo: bool) -> list[str]: ...
def honesty_payload(account, *, promo_remaining, soft_pct=10, hard_pct=30) -> dict: ...
```

- [ ] **Step 1: Unit tests** for util 500/1000=50%, amount to 30% = 200, interest 12% APR on 1200 → 12.00/mo

- [ ] **Step 2: Implement + commit** `feat: card honesty util interest advice`

---

### Task 4: Autopay recompute uses timing

**Files:**
- Modify: `src/honestspend/services/autopay.py`
- Test: `tests/test_autopay_recompute.py` or `tests/test_pay_timing.py`

**Interfaces:**
- `POLICIES` includes `books`
- Schedule `next_date` from `proj["next_due"]` (already) once projection uses timing
- Notes include `timing={timing}`

- [ ] **Step 1: Test** books + day_before_close → schedule date and amount

- [ ] **Step 2: Wire POLICIES; ensure project is single source for date**

- [ ] **Step 3: Commit** `feat: cash schedule respects pay timing and books`

---

### Task 5: API cycle-config + honesty block

**Files:**
- Modify: `src/honestspend/api/app.py`
- Test: `tests/test_statement_cycle_api.py`

**Interfaces:**
- CycleConfigIn: `payment_timing` optional
- GET cycle includes honesty + warnings + payment_timing
- Validate timing in TIMINGS

- [ ] **Step 1–3: Tests + implement + commit** `feat(api): payment_timing and honesty on cycle`

---

### Task 6: WinUI Credit policy timing honesty

**Files:**
- Modify: `LedgerApiClient.cs`, `CreditPage.xaml(.cs)`, `UiCopy.cs` if needed

**UI:**
- Policy: … + Pay current balance (`books`)
- Timing combo: On due day / Day before statement closes / On statement close day
- Panel: min, est interest, util %, chips “Pay to 30%” / “Pay to 10%” (sets fixed + amount, save)
- Expandable advice text from API

- [ ] **Step 1: Wire client fields**

- [ ] **Step 2: UI + save path includes payment_timing**

- [ ] **Step 3: Build Release x64 + commit** `feat(winui): pay timing and honesty panel`

---

### Task 7: Docs + e2e

**Files:**
- Modify: `docs/STATEMENT_CYCLES.md`, `CHANGELOG.md`
- Test: `tests/test_pay_policies_e2e.py`

- [ ] **Step 1: E2E** charge → books → day_before_close → IFPP counts cash on that date path

- [ ] **Step 2: Docs** — policies table, timing, advice disclaimer, budgets for food/gas not pad

- [ ] **Step 3: Commit** `docs+test: pay policies honesty`

---

## Success criteria

| Check | Task |
|-------|------|
| books full balance | T2 |
| close / day before dates | T2 |
| schedule live | T4 |
| honesty math | T3 |
| API | T5 |
| Credit UI | T6 |
| Safe honesty | T7 |

## Execution Handoff

**1. Subagent-Driven (recommended)**  
**2. Inline Execution**
