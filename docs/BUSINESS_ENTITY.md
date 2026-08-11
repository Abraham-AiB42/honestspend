# Business entity (AP Agency steals)

Portable patterns from multi-entity agency books for **any** business Who in HonestSpend: versioned bills, clear pay-from, owner draws, thin payroll day, fixed/variable opex, income source tags, and year P&L — without carrier matrices, full HR/payroll, or day grids.

See also: [SIMPLE_MODE.md](./SIMPLE_MODE.md) (Coming up / cash-side), [STATEMENT_CYCLES.md](./STATEMENT_CYCLES.md) (card pay-from).

## Goals

| Steal | HonestSpend |
|-------|-------------|
| Recurring rent with amount steps | **Versioned series** on scheduled bills (`series_id` + start/end windows) |
| Account column (Chase / Amex) | **Pay from** = `account_id` (cash **or** credit) + optional **vendor** |
| Owner column ≠ expenses | Schedule **`kind=owner_draw`** (outflow, never a payday) |
| Payroll + employer tax same day | **Payroll package** → two linked expense schedules |
| Fixed vs variable opex | `opex_class` = `fixed` \| `variable` (business) |
| Income by source | `income_source` on income schedules |
| EOY multi-year totals | **Entity year P&L** from the ledger |

Product for **everyone** with a business entity. Multi-entity silo default (Business Who). Personal entities skip forced payroll UI; `opex_class` stays optional.

## Plain language

| Say | Meaning |
|-----|---------|
| **Pay from** | Account that funds the bill (checking **or** card) |
| **Vendor** | Payee name on the schedule |
| **Starts / Ends** | Effective window for this bill version |
| **Owner draw** | Owner takes money out — not an expense, not income |
| **Payroll day** | Net pay + employer tax both leave cash on the same date |
| **Safe to spend** | Cash honesty after bills, buffers, tax vault, … |

Never expose engine jargon (IFPP, horizon, series UUIDs) in Simple UI copy.

---

## Versioned bills (rent steps)

One logical bill (e.g. **Office rent**) can have **multiple segments** that share a `series_id` and non-overlapping date windows:

| series_label | amount | start | end |
|--------------|--------|-------|-----|
| Office rent | −2000 | 2026-04-16 | 2026-07-28 |
| Office rent | −2100 | 2026-07-29 | 2027-07-30 |
| Office rent | −2200 | 2027-07-29 | null |

- Expand includes an occurrence date `d` only when  
  `(start_date is null or d ≥ start_date)` and `(end_date is null or d ≤ end_date)`.
- Prefer **non-overlapping windows** so history stays visible; expand filters to the matching segment.
- **Change amount on date…** closes the open segment (`end_date = effective_from − 1 day`) and inserts the next step.

### Annual lumps

No special type. Pattern: yearly cadence, or monthly with a short start/end window (e.g. BOP Aug 2–5).

### API

- Schedule create/patch: `start_date`, `end_date`, `series_id`, `series_label`, `vendor`
- `GET /api/scheduled/series?profile_id=` — group segments by series
- `POST /api/scheduled/series/step` — body `{series_id, new_amount, effective_from, end_date?, next_date?}`

---

## Pay from cash vs card

| Pay from | Coming up / Safe | Notes |
|----------|------------------|-------|
| **Cash** (checking/savings) | Schedule amount is a cash outflow | Appears on Simple **Coming up** |
| **Credit** (card) | **Not** a cash drain until the card payment runs | Netflix-on-card skipped from cash Safe; only **`Card payment · {nickname}`** on the pay-from checking account hits cash |

Same honesty rule as statement cycles: schedules on **credit** accounts never drain Safe until cash leaves for the card. UI always says **Pay from**, not account ids.

---

## Owner draw

- Schedule `kind=owner_draw`, amount negative.
- **Coming up** / cash runway: **outflow** (direction `out`).
- **Never** treated as income or a payday for window logic.
- Entity P&L rolls draws into **`owner_draws`** (equity), not expenses.

Use for regular owner distributions / “pay myself from the business” — pair with Move money / playbooks when money crosses entities.

---

## Payroll package (thin)

Not a payroll engine. One action creates **two** expense schedules on the **same** pay date, cadence, and cash account:

1. `{name} · net` — net pay out  
2. `{name} · employer tax` — employer tax out  

Both notes include `package_id=<uuid>`. Coming up shows both on payday → Safe drops by **net + tax**.

Cadence: `biweekly` · `semimonthly` · `monthly` · `weekly`.

### API

- `POST /api/payroll-packages`  
  Body: `profile_id`, `name`, `pay_date`, `cadence`, `net_payroll`, `employer_tax`, `cash_account_id`, optional `series_label`  
  Returns: `{package_id, net_id, tax_id}`

No employee roster, commissions matrix, or Paychex import in v1.

---

## opex_class / income_source

| Field | Values | Use |
|-------|--------|-----|
| `opex_class` | `fixed` \| `variable` \| null | Business expense schedules; optional filter / fixed-opex reserve notes. Personal → usually null |
| `income_source` | free tag (e.g. `farmers_efolio`, `other`) | Income schedules only; list/API filter — **not** a carrier line catalog |

Set on scheduled create/patch. Business UI may show Fixed / Variable when entity type is business; personal entities leave them off.

---

## Entity year P&L

Ledger rollup for one Who and calendar year — not a day-grid Balance sheet.

| Bucket | Rule (high level) |
|--------|-------------------|
| **income** | Income categories / positive non-transfer credits |
| **payroll** | Payroll budget group / name hints (`payroll`, package · net) |
| **taxes** | Tax categories, employer tax, tax vault outflows |
| **owner_draws** | Owner / equity categories or owner-draw payees |
| **expenses** | Other outflows |
| **net** | Signed sum of buckets |
| **by_category** | Top categories |

Transfers and system intermix codes are skipped. No invented carrier product lines.

### API / UI

- `GET /api/reports/entity-pnl?profile_id=&year=` (year defaults to current)
- Full books → **Reports** → Entity year P&L (selected entity)

---

## Explicit non-steals

HonestSpend deliberately does **not** productize:

| Out of scope | Why |
|--------------|-----|
| **Farmers e-Folio matrix** (NB / Service line catalog) | Carrier-specific; we only allow a generic **income source** tag |
| **Full payroll / HR** | No W-2 engine, commissions matrix, employee start/term roster, Paychex import |
| **State tax tables as product** | Employer tax is a **fixed amount** you enter on the package |
| **Excel day-grid Balance / Expenses UI** | Ledger + Coming up / cash runway instead |
| **Personal Budget.xlsx features already shipped** | Budgets, statement cycles, etc. live in their own docs |

If you need true payroll or carrier commission books, keep that system — use HonestSpend for **cash honesty**, schedules, and tax-ready year totals.

---

## Acceptance (contract)

| # | Scenario | Expect |
|---|----------|--------|
| A | Rent series $2k then $2.1k from July 29 | Expand mid-first segment → 2000; mid-second → 2100 |
| B | Utility on card `account_id` | Not in cash Coming up; **Card payment** is |
| C | Owner draw monthly | Coming up outflow; not a payday |
| D | Payroll package biweekly | Two lines same date; Safe drops by net+tax |
| E | Entity P&L year | Income / expense / draw buckets match seeded ledger |
| F | Personal entity | `opex_class` optional; no forced payroll UI |

---

## Implementation map

| Area | Path |
|------|------|
| Schema | `ScheduledItem`: `start_date`, `series_id`, `series_label`, `vendor`, `opex_class`, `income_source`; `kind` includes `owner_draw` |
| Series | `services/schedule_series.py` |
| Expand window | `engine/schedule_expand.py` (+ Coming up / IFPP / runway callers) |
| Payroll package | `services/payroll_package.py` |
| Entity P&L | `services/entity_pnl.py` |
| API | schedule CRUD + series + payroll-packages + reports/entity-pnl |
| UI | Bills / Add bill: pay from, vendor, starts/ends, owner draw, series step; Reports P&L; optional business payroll day |
