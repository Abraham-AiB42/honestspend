# HonestSpend — Design Document

**Product name:** HonestSpend  
**Distribution:** Open source freeware (small business owners + anyone)  
**Platform:** Local-first; Windows first → Mac / Linux / iOS / Android  
**North star:** Interest-Free Purchasing Power (IFPP)  
**Constitution:** [PRODUCT.md](./PRODUCT.md)

> What can I spend or charge *right now* without going negative on checking, without fees, and without unnecessary interest — while using intentional 0% float when fiscally sound?

---

## 1. Product summary

A local multi-entity HonestSpend for small business owners: books + liquidity cockpit.

| Profile | Role |
|---------|------|
| **Personal** | Checking never-neg + credit float game |
| **Business entities** | e.g. S-corp / LLC books, linked when intermixed |

**Priorities:** (1) fiscal soundness (2) credit health.  
**UX:** minimum user time — automation first.

**Out of scope (now):** kids products, investing UI, payroll connectors, affiliate marketplaces.

---

## 2. Key decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| License | Open source freeware | PRODUCT.md |
| North star | Never-neg checking + intentional 0% float | User strategy |
| Safety buffer | Default **$1,000**, user unlimited | PRODUCT.md |
| Never negative | **Checking** hard | Non-negotiable |
| Opportunity cost | On by default | Cheap debt vs yield |
| Advice | Options + rec + reasoning | Override OK |
| Fiscal vs credit | Fiscal #1, credit #2 | PRODUCT.md |
| Live data | Max integration | Operational truth |
| Payroll | Numbers only | No Gusto/etc. |
| Tax | Multi-state capable | PRODUCT.md |
| Backup | Local | Privacy |
| Clients | All platforms inevitable | API-first |
| Sharing | Permission stack | CPA/bookkeeper |
| Variable income | Optional configurable | Not forced |
| Spreadsheet | One-time migrate only | Self-contained |
| Categories | IRS-mapped COA | Tax packet |

---

## 3. Architecture

```
┌──────────────────────────────────────────────┐
│  Tray: Spendable Now | Card risk | Next bill │
└──────────────────────────────────────────────┘
┌───────────────┐     ┌────────────────────────┐
│ Desktop UI    │────▶│ Engine                 │
│ 3 profiles    │     │ IFPP / schedule / cards│
│ ledger, bills │     │ Categorizer + Grok     │
└───────────────┘     │ Multi-ledger + intermix│
                      └───────────┬────────────┘
                                  │
              ┌───────────────────┼──────────────────┐
              ▼                   ▼                  ▼
        SQLite (local)     Sync (Plaid/CSV)    Optional Stripe/Azure
        + OneDrive backup
```

**Stack (v1):**
- Python 3.11+ package `honestspend`
- SQLite via SQLAlchemy
- FastAPI local API
- Static web UI (served by FastAPI)
- Later: Tauri/Electron wrapper + real tray

---

## 4. Data model (core)

### 4.1 Profiles
`id`, `slug` (auto from display name), `display_name`, `entity_type` (individual | business | child), `tax_form_primary` (1040 | 1120S | 1065 | SchC | none), optional `parent_profile_id` for children. Fresh install seeds **Personal only**; businesses and children via Add Business / Add Child.

### 4.2 Accounts
Bank, credit, cash, liability, investment.  
Fields: `profile_id`, `kind`, `institution`, `nickname`, `current_balance`, `available_credit`, `plaid_item_id?`, `is_cash_for_ifpp`, `include_in_net_worth`

### 4.3 Credit cards (extends account)
`statement_close_day`, `payment_due_day`, `apr`, `promo_apr`, `promo_end_date`, `promo_balance`, `min_payment`, `rewards_program`, `utilization_warn_pct`

### 4.4 Categories (Chart of Accounts)
See `src/honestspend/data/tax_coa.json`.  
Every category: `display_name`, `profile_scope`, `tax_form`, `tax_line`, `deductibility`, `partial_rule`, `parent_id`, `budget_group`

### 4.5 Transactions
`date`, `amount`, `account_id`, `profile_id`, `category_id?`, `payee`, `memo`, `status` (pending|cleared|reconciled), `confidence`, `external_id`, `receipt_path?`, `is_transfer`, `transfer_pair_id?`

### 4.6 Recurring / schedule
Bills and expected income: `amount`, `cadence`, `next_date`, `account_id?`, `category_id?`, `certainty` (fixed|expected|historical_avg)

### 4.7 Settings
`ifpp_mode` (conservative|expected), `safety_buffer`, `never_negative_scope` (checking|all_cash|all_cash_plus_full_pay), `vantage_util_warn` (10/30)

---

## 5. IFPP engine

### Inputs
- Cash balances on accounts flagged `is_cash_for_ifpp`
- Scheduled outflows (bills) through horizon
- Scheduled inflows (if mode = expected)
- Safety buffer
- Per-card: balance, available credit, statement close, due date, promo end, promo balance

### Outputs
```json
{
  "as_of": "ISO-date",
  "mode": "conservative|expected",
  "cash_spendable": 0,
  "card_float_interest_free": 0,
  "combined_purchasing_power": 0,
  "cards": [{ "account_id", "safe_to_charge", "payoff_plan", "risk" }],
  "next_red_day": null,
  "warnings": []
}
```

### Rules
1. **Conservative:** only cleared cash − buffer − bills due before next known inflow (or horizon).
2. **Expected:** include scheduled/historical income with certainty weights.
3. **Card float:** amount that can be charged and paid in full by due date *or* held under 0% until promo end (min pays until last promo month, then balloon).
4. **Never** recommend revolving at standard APR.
5. **Rewards** rank only among cards that pass the safety test.
6. Utilization warnings vs configured Vantage-style thresholds.

### Acceptance tests (must never fail)
1. Missed bill / wrong due date → red
2. Double-counted transfer → red
3. Card charge double-counted as spend + balance → red
4. 0% promo treated as APR debt → red
5. Expected income in conservative mode → red
6. “Safe to charge” without full-pay path → red

---

## 6. Tax Chart of Accounts

Locked seed: `src/honestspend/data/tax_coa.json`.

- **Business profiles:** Form 1120-S lines 1–22 + line 20 detail + K-1 separately stated tags; Schedule C crosswalk on each expense.
- **Personal:** budget groups + Schedule A / 1040 adjustments where real; else `tax_form: none`.
- **System:** transfer, CC payment, loan principal, distribution, reimbursement, uncategorized.

Meals default `partial_rule: meals_50`. Capital assets → depreciation path, not supplies.

---

## 7. Intermix tools

When personal/business money mixes, UI offers:
1. Mark needs reimbursement  
2. Create transfer between profiles  
3. Split transaction across categories/profiles  

Owner salary → payroll categories. Distributions → equity, not expense.

---

## 8. Tax Packet export

Per profile, calendar year:
- `transactions.csv`
- `expenses_by_tax_line.csv`
- `income_by_type.csv`
- `owner_activity.csv`
- `year_summary.md`

Disclaimer: not tax advice; assists TurboTax / CPA entry.

---

## 9. Phased PR plan

| PR | Title | Deliverable |
|----|--------|-------------|
| PR1 | Scaffold + COA + schema | Package, SQLite, tax seed, migrations |
| PR2 | IFPP engine + unit tests | Spendable Now pure functions |
| PR3 | Local API + ledger CRUD | FastAPI, profiles, accounts, txns |
| PR4 | Web shell + Spendable dashboard | One-ring UI |
| PR5 | Card promo + payoff planner | Credit game |
| PR6 | Smart categorize hooks (Grok) | Rules + AI |
| PR7 | CSV import + Tax Packet | Replace Excel path |
| PR8 | Plaid connect | Full bank link |
| PR9 | Tray + desktop wrapper | Always-on number |

---

## 10. Open questions (defaults applied)

| Topic | Default until changed |
|-------|------------------------|
| Safety buffer | $0 (configurable) |
| Desktop wrapper | Browser localhost first; tray later |
| Plaid production keys | Dev/sandbox until user supplies |
| Exact card close/due | Entered at first-run setup |

---

## 11. Non-goals (v1)

- Mobile app  
- Commission pipeline  
- Kids banking product  
- Replacing 1120-S e-file (books + packet only)  
- Investment trading  
- Auto-moving money (suggestions only)
