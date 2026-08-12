# Account Running Balance & Statement Cycles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every account keeps a trustworthy **running books balance** and, for credit cards (and other cycle-based liabilities), a **projected statement balance** driven by **close day + due day + pay policy**, so cash Safe to spend always sees the right next payment — better than the owner’s Budget.xlsx day-grid.

**Architecture:** Pure Python services own the math (no client-side balance logic). `Account` gains cycle/funding config; new `statement_cycles` + optional `promo_installment_lines` tables hold history; a single `recompute_account_cycle(session, account_id)` runs after any books mutation (txn create/patch/void, import, Plaid, reconcile). Autopay schedules become **cash-funded** payments whose **amount is rewritten** on every recompute. IFPP continues to skip credit-account expense schedules and uses the cash payment schedule for runway.

**Tech Stack:** Python 3.11+, SQLAlchemy SQLite, FastAPI, existing `ScheduledItem` / `Account` / IFPP, pytest, WinUI Full books Credit page + Simple Home “Do this next” only for urgency.

**Inspiration (owner Budget.xlsx — improve, don’t clone the grid):**
- **Credit Cards** sheet: day activity → running balance (`F = E + prev F`); header balance = sum to today.
- **Debt** sheet: per-card **Due Date** + **Close Date**.
- **Budget** due-day cells: payment `= -running_balance[+ promo adjustments]` e.g. `P2487 = -'Credit Cards'!F2460+ISB!B4-ISB!C4+10`.
- **ISB** tab: multi-line 0% principal remaining + monthly installment (deferred vs payment).

## Global Constraints

- Local-first freeware; never bounce checking (never-neg on cash payment creates).
- Intentional 0% float is first-class; statement pay must not force full promo principal when installments exist.
- Simple mode: no day-grid UI; surface **next payment amount + due date** on Credit/Home only.
- Card-first: charges may live on credit; cash pays the card on due day from **customer-chosen funding account**.
- Config sources (priority): customer override > learned from imports/Plaid > defaults.
- TDD: each task starts with failing tests; no silent balance drift.
- Version: ship behind existing 1.0.x; bump schema via `migrations.py` next integer.
- Copy: “Safe to spend”, “statement balance”, “next payment” — never IFPP in UI.

---

## File map

| Path | Responsibility |
|------|----------------|
| `src/honestspend/db.py` | Columns + new tables |
| `src/honestspend/migrations.py` | Schema migration |
| `src/honestspend/services/account_ledger.py` | **New** — running balance from ledger (rebuild/verify) |
| `src/honestspend/services/statement_cycle.py` | **New** — cycle windows, projected statement, payment amount |
| `src/honestspend/services/promo_installments.py` | **New** — ISB-like lines (optional phase 2 in same plan Tasks 6–7) |
| `src/honestspend/services/autopay.py` | Recompute + cash-funded schedule sync |
| `src/honestspend/services/account_balance.py` | Hook recompute after apply |
| `src/honestspend/api/app.py` | API endpoints |
| `src/honestspend/services/ifpp_service.py` | Use cash payment schedules; already skips credit expense schedules |
| `clients/HonestSpend.WinUI/Pages/CreditPage.*` | Show cycle + next payment + pay-from |
| `clients/HonestSpend.WinUI/Pages/AccountsPage.*` | Close/due/funding edit |
| `tests/test_statement_cycle.py` | Core cycle math |
| `tests/test_account_ledger.py` | Running balance integrity |
| `tests/test_autopay_recompute.py` | Payment schedule refresh on charge |
| `docs/STATEMENT_CYCLES.md` | Product/engine doc |

---

## Domain model (locked for implementers)

### Account fields (add)

| Field | Type | Meaning |
|-------|------|---------|
| `statement_close_day` | int 1–31 (exists) | Day cycle closes |
| `payment_due_day` | int 1–31 (exists) | Day cash payment is due |
| `autopay_policy` | str (exists) | `none` \| `min` \| `statement` \| `promo_sink` \| `fixed` |
| `payment_fixed_amount` | Decimal (exists) | When policy=`fixed` |
| `payment_funding_account_id` | FK accounts, nullable | **Cash/checking that pays this card** |
| `cycle_config_source` | str | `user` \| `import` \| `plaid` \| `default` |
| `statement_balance_cached` | Decimal nullable | Last computed **open cycle projected** statement |
| `next_payment_amount_cached` | Decimal nullable | Amount due on next due date under policy |
| `next_payment_date_cached` | date nullable | Next due date |

### Running balance

- **Source of truth for display and payments:** `Account.current_balance` after all applies.
- **Verification:** `rebuild_running_balance(session, account_id)` = sum of non-void txns from open/zero with credit sign convention matching `apply_amount_to_account`.
- Do **not** store a separate day-grid; recompute from ledger (better than Excel: import/Plaid/manual all feed one ledger).

### Statement cycle (credit)

For `as_of` date:

1. **Last close date** ≤ as_of using `statement_close_day` (clamp 28–31 to month end).
2. **Next close date** after that.
3. **Next due date** ≥ as_of using `payment_due_day` (existing `next_due_date` helper).
4. **Open cycle charges** = sum of card **purchases** (signed increase to owed) with `txn_date` in `(last_close, next_close]` that are not payments/credits.  
   **Simpler v1 (matches Excel running-balance style and is more honest for statement-pay):**  
   **Projected statement balance** = `max(0, current_balance − promo_principal_remaining + promo_installments_due_this_cycle)` when policy needs carve-out; else for pure statement = `max(0, current_balance)` at last_close if we had snapshot, **OR** live:  
   **v1 recommendation (ship first):**  
   - `projected_statement = max(0, current_balance − sum(open promo line remainings))`  
   - `next_payment = f(policy, projected_statement, min_payment, fixed, promo_installments_due)`  
   - When a **real statement** is imported (optional later): store `statement_cycles` row with `actual_balance` and freeze that cycle.

### Payment formula (v1 — better than static Excel cells)

```
promo_remaining = sum(line.principal_remaining for open promo lines on card)
promo_due_this_month = sum(line.monthly_payment for open lines with due in this cycle)

if policy == "statement":
    next_payment = max(0, current_balance - promo_remaining) + promo_due_this_month
elif policy == "min":
    next_payment = min_payment or max(25, 0.02 * current_balance)
elif policy == "fixed":
    next_payment = payment_fixed_amount
elif policy == "promo_sink":
    next_payment = promo_due_this_month  # or existing promo_sink monthly; prefer lines if present
else:
    next_payment = 0
```

This matches the spirit of `Budget!P2487 = -running + ISB.Deferred - ISB.Payment (+ pad)` without requiring a day grid.

### Cash payment schedule

- Name: `Card payment · {nickname}` (stable).
- `account_id` = **funding cash account** (not the card).
- `amount` = `-next_payment`.
- `next_date` = next due date.
- `notes` include `card_account_id={id}; policy={policy}; auto=statement_cycle`.
- IFPP: cash schedule **does** hit cash runway; card charge schedules on credit **do not** (already implemented).

### Config learning

| Signal | Action |
|--------|--------|
| User sets close/due/funding/policy in UI | `cycle_config_source=user` — never overwrite with import |
| CSV/OFX/Plaid import of credit account | If source ≠ user and field empty: set due/close from repeated payment cadence / filename heuristics when available; else leave null |
| Manual account create | Defaults: due=15, close=1, policy=statement, funding=default checking |

---

### Task 1: Schema — funding account + cycle cache columns + statement_cycles table

**Files:**
- Modify: `src/honestspend/db.py` (Account + new models)
- Modify: `src/honestspend/migrations.py` (next migration id)
- Test: `tests/test_migrations.py` (or new `tests/test_statement_cycle_schema.py`)

**Interfaces:**
- Produces: `Account.payment_funding_account_id`, `cycle_config_source`, `statement_balance_cached`, `next_payment_amount_cached`, `next_payment_date_cached`
- Produces: model `StatementCycle` with fields below

```python
# StatementCycle
# id, account_id, cycle_start, cycle_end (close date), due_date
# projected_balance, actual_balance (nullable until statement import)
# payment_amount, payment_funding_account_id
# status: open | closed | paid
# source: projected | import | plaid | user
```

- [ ] **Step 1: Write failing migration/schema test**

```python
def test_account_has_funding_and_cycle_cache(tmp_path, monkeypatch):
    # init_db / migrate; create Account; set payment_funding_account_id
    assert hasattr(Account, "payment_funding_account_id")
    assert hasattr(Account, "statement_balance_cached")
```

- [ ] **Step 2: Run test — expect fail**

Run: `pytest tests/test_statement_cycle_schema.py -v`

- [ ] **Step 3: Implement columns + migration + StatementCycle model**

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add src/honestspend/db.py src/honestspend/migrations.py tests/test_statement_cycle_schema.py
git commit -m "feat(schema): statement cycle cache and funding account on cards"
```

---

### Task 2: Running balance rebuild (ledger integrity)

**Files:**
- Create: `src/honestspend/services/account_ledger.py`
- Test: `tests/test_account_ledger.py`

**Interfaces:**
- Produces: `rebuild_running_balance(session, account_id: int) -> Decimal`
- Produces: `verify_running_balance(session, account_id: int) -> dict` with `books`, `rebuilt`, `delta`, `ok`

```python
def rebuild_running_balance(session, account_id: int) -> Decimal:
    """Sum non-void txns with same sign rules as apply_amount_to_account; write Account.current_balance."""
```

- [ ] **Step 1: Failing test** — create card, post charges/payment txns, corrupt balance, rebuild restores.

```python
def test_rebuild_running_balance_credit(tmp_path, monkeypatch):
    # charge -100 (books +100 owed), payment +40 (books -40 owed) → balance 60
    ...
    cash.current_balance = Decimal("999")  # corrupt
    assert rebuild_running_balance(s, card.id) == Decimal("60.00")
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement `account_ledger.py`**

- [ ] **Step 4: Run — pass**

- [ ] **Step 5: Commit** `feat: rebuild running balance from ledger`

---

### Task 3: Cycle window + projected statement + next payment math

**Files:**
- Create: `src/honestspend/services/statement_cycle.py`
- Test: `tests/test_statement_cycle.py`
- Reuse: `honestspend.engine.ifpp.next_due_date`

**Interfaces:**
- Produces:

```python
def previous_close_date(as_of: date, close_day: int) -> date: ...
def next_close_date(as_of: date, close_day: int) -> date: ...
def compute_next_payment(
    *,
    policy: str,
    current_balance: Decimal,
    min_payment: Decimal | None,
    fixed_amount: Decimal | None,
    promo_remaining: Decimal = Decimal("0"),
    promo_due: Decimal = Decimal("0"),
) -> Decimal: ...

def project_card_payment(session, account_id: int, *, as_of: date | None = None) -> dict:
    """Returns statement_balance, next_payment, next_due, last_close, next_close, funding_account_id, policy."""
```

- [ ] **Step 1: Failing tests**

```python
def test_close_and_due_windows():
    # close_day=18, as_of=2026-09-15 → last close 2026-08-18, next close 2026-09-18
    ...

def test_statement_payment_carves_out_promo():
    # balance 5000, promo_remaining 2000, promo_due 200 → payment 3200
    assert compute_next_payment(
        policy="statement",
        current_balance=Decimal("5000"),
        min_payment=None,
        fixed_amount=None,
        promo_remaining=Decimal("2000"),
        promo_due=Decimal("200"),
    ) == Decimal("3200.00")
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement pure date + payment math + `project_card_payment` reading Account**

- [ ] **Step 4: Run — pass**

- [ ] **Step 5: Commit** `feat: statement cycle projection and payment formula`

---

### Task 4: Recompute hook + cash-funded autopay schedule sync

**Files:**
- Modify: `src/honestspend/services/autopay.py`
- Modify: `src/honestspend/services/account_balance.py` (or call sites in `app.py` after commit)
- Test: `tests/test_autopay_recompute.py`

**Interfaces:**
- Produces: `recompute_card_payment_schedule(session, account_id: int, *, as_of: date | None = None) -> dict`
- Produces: `recompute_all_card_payments(session, profile_id: int | None = None) -> int`
- Modifies: `_sync_schedule` to set `account_id=funding_cash_id`, name `Card payment · {nickname}`, amount from `project_card_payment`
- After any credit balance change: call recompute for that account

```python
def recompute_card_payment_schedule(session, account_id: int, *, as_of=None) -> dict:
    proj = project_card_payment(session, account_id, as_of=as_of)
    # update Account.*_cached fields
    # upsert ScheduledItem on funding account with amount=-proj["next_payment"]
    # return proj + schedule ids
```

- [ ] **Step 1: Failing test**

```python
def test_charge_updates_next_payment_amount(tmp_path, monkeypatch):
    # card policy=statement, funding=checking, balance 100 → schedule amount -100
    # post charge increasing owed by 50 → schedule amount -150, next_date still due day
    ...
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement recompute + wire after `apply_amount_to_account` when account.kind==credit**

Prefer a single helper `after_account_balance_changed(session, account_id)` called from:
- `app.py` create_transaction / patch_transaction / void
- `bank_csv.py` / `bank_ofx.py` after batch
- `plaid_service` after balance sync if applicable

- [ ] **Step 4: Run — pass** (also run `tests/test_ifpp_credit_bill_schedule.py` — cash payment on checking must still count in IFPP)

- [ ] **Step 5: Commit** `feat: live card payment schedule from statement projection`

---

### Task 5: API + settings surface

**Files:**
- Modify: `src/honestspend/api/app.py`
- Test: `tests/test_statement_cycle_api.py` (use existing TestClient fixture pattern from `test_api_http.py`)

**Interfaces:**
- `GET /api/accounts/{id}/cycle` → projection dict
- `GET /api/accounts/cycles` → list for profile/scope
- `PUT /api/accounts/{id}/cycle-config` body: `statement_close_day`, `payment_due_day`, `autopay_policy`, `payment_funding_account_id`, `payment_fixed_amount` → sets `cycle_config_source=user`, recomputes
- `POST /api/accounts/{id}/recompute-cycle` → force rebuild

- [ ] **Step 1: Failing API tests** for GET projection + PUT config

- [ ] **Step 2: Implement routes**

- [ ] **Step 3: Pass tests**

- [ ] **Step 4: Commit** `feat(api): statement cycle endpoints`

---

### Task 6: Promo installment lines (ISB better)

**Files:**
- Create: `src/honestspend/services/promo_installments.py`
- Modify: `db.py` + migrations (`PromoInstallmentLine`)
- Modify: `statement_cycle.compute_next_payment` inputs from lines
- Test: `tests/test_promo_installments.py`

**Interfaces:**

```python
# PromoInstallmentLine: account_id, name, principal_remaining, monthly_payment,
# start_date, end_date?, active, source

def open_promo_totals(session, account_id: int, *, as_of: date) -> tuple[Decimal, Decimal]:
    """(principal_remaining, monthly_due)"""

def apply_month_roll(session, account_id: int, *, as_of: date) -> None:
    """principal_remaining = max(0, principal - monthly); deactivate when zero"""
```

- [ ] **Step 1: Failing test** — line 823.78 / 12 monthly; after roll remaining decreases; statement payment uses carve-out

- [ ] **Step 2: Implement table + service + hook into `project_card_payment`**

- [ ] **Step 3: API CRUD** `GET/POST /api/accounts/{id}/promo-lines`, `POST .../promo-lines/{id}/roll` (or automatic on cycle close)

- [ ] **Step 4: Commit** `feat: promo installment lines (ISB-class)`

---

### Task 7: Import / Plaid config hints (non-destructive)

**Files:**
- Modify: `src/honestspend/services/bank_csv.py` / plaid account upsert (where accounts created)
- Modify: `setup_discover.py` card apply to set funding default + policy
- Test: `tests/test_cycle_config_learn.py`

**Rules:**
- If `cycle_config_source == "user"`, never auto-change close/due/funding/policy.
- On new credit account: default policy `statement`, funding = profile’s first checking, close/due from body if present else 1/15.
- Optional: detect “Payment to {card}” history on cash to suggest funding account.

- [ ] **Step 1: Failing test** — user source frozen; import creates card with defaults

- [ ] **Step 2: Implement**

- [ ] **Step 3: Commit** `feat: cycle config defaults and import-safe learning`

---

### Task 8: WinUI — Credit + Accounts (Full books); Simple Home whisper only

**Files:**
- Modify: `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- Modify: `clients/HonestSpend.WinUI/Pages/CreditPage.xaml(.cs)`
- Modify: `clients/HonestSpend.WinUI/Pages/AccountsPage.xaml(.cs)` (optional compact)
- Modify: `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs` — optional one line under float whisper: “Next card payments: $X on {date}” from cycles list if urgency

**UI requirements:**
- Per card: Close day · Due day · Policy · Pay from (cash picker) · **Projected statement** · **Next payment** · Recompute button
- Promo lines list: add/edit remaining + monthly
- Plain language only

- [ ] **Step 1: Wire API client methods**

- [ ] **Step 2: Credit page section “Statement & payment”**

- [ ] **Step 3: Manual dogfood** — set Amazon-like close 18 / due 15 / statement / funding checking; post charges; confirm next payment increases; IFPP Safe drops by payment amount in horizon

- [ ] **Step 4: Commit** `feat(winui): statement cycle and live card payment UI`

---

### Task 9: Docs + Simple mode contract

**Files:**
- Create: `docs/STATEMENT_CYCLES.md`
- Modify: `docs/PRODUCT.md` (one row under Spendable / credit)
- Modify: `docs/SIMPLE_MODE.md` (what Simple shows)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write docs** explaining Excel parity + improvements (no day grid, multi-entity, promo lines, funding cash, recompute hooks)

- [ ] **Step 2: Commit** `docs: statement cycles product model`

---

### Task 10: Integration dogfood tests (engine)

**Files:**
- Create: `tests/test_statement_cycle_e2e.py`

- [ ] **Step 1: Scenario test**

```python
def test_card_first_statement_pay_affects_safe(tmp_path, monkeypatch):
    # checking 5000, buffer 0
    # card policy statement, funding checking, due in 5 days, balance 0
    # charge card 800 → next_payment 800, cash schedule -800 on due date
    # run_ifpp → cash_spendable reflects that outflow within horizon
    # add promo line remaining 300 monthly 50 with balance 800 → payment = 800-300+50 = 550
```

- [ ] **Step 2: Pass full relevant suite**

```bash
pytest tests/test_statement_cycle.py tests/test_account_ledger.py tests/test_autopay_recompute.py tests/test_promo_installments.py tests/test_statement_cycle_e2e.py tests/test_ifpp_credit_bill_schedule.py -q
```

- [ ] **Step 3: Commit** `test: statement cycle e2e card-first Safe to spend`

---

## Out of scope (explicit)

- Excel day-grid UI or 3000-row calendar tables  
- Automatic bank statement PDF parsing for cycle close (manual/import actual later is enough)  
- Paying the card for the user (we schedule; user pays bank)  
- Changing Simple mode into a full credit desk  

## Success criteria (better than Budget.xlsx)

| Excel today | HonestSpend after this plan |
|-------------|-----------------------------|
| Manual day grid + fragile `INDIRECT`/`MATCH` | Ledger-native running balance |
| Payment cells hand-edited per due day | Auto schedule amount on every charge |
| ISB tab manual lines | First-class promo lines + API/UI |
| Single workbook, one household | Multi-entity profiles + silo Safe |
| No Safe-to-spend engine | Cash payment in IFPP horizon automatically |
| Config only in cells | User override + import defaults + Plaid-ready |

## Self-review (spec coverage)

| Requirement | Task |
|-------------|------|
| Every account running balance | T2 |
| Statement balance from close dates | T3 |
| Payment dates | T3–T4 |
| Auto config import/Plaid/manual | T7 |
| Customer override | T5, T7 |
| Better than Excel (live recompute, funding cash, no grid) | T4, T8, success table |
| Promo ISB-class | T6 |
| Safe to spend honesty | T4, T10 |

No TBD placeholders. Types consistent: `project_card_payment` → `recompute_card_payment_schedule` → API/UI.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-11-account-statement-balances.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — this session, executing-plans with checkpoints  

**Which approach?**
