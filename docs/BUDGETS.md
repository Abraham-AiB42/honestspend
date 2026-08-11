# Period budgets (v1.0.34)

Excel **Budget.xlsx** parity: daily (workdays), weekly, and monthly **plans** with history-based suggestions and Safe to spend **reserve**.

## Model

| Period | Window | Default history for suggestions |
|--------|--------|----------------------------------|
| **daily** | Today if workday | ~90 calendar days of **workdays** |
| **weekly** | Week containing today (Mon start default) | **24 weeks** |
| **monthly** | Calendar month | **18 months** |

- **Actual** = sum of expense txns in category (excludes transfers).  
- **Remaining** = max(0, plan − actual); **no carryover**.  
- **Reserve** = sum of remaining on active budgets → subtracted from Safe to spend when `budget_reserve_enabled` (default on).  
- **Cuts** free reserve: skip N workdays, scale weekly remaining, release monthly remaining.

## API

- `GET/POST /api/budgets`, `DELETE /api/budgets/{id}`  
- `GET /api/budgets/status`, `/suggestions`, `/cuts`  
- `POST /api/budgets/cuts/apply`, `/suggestions/accept`  
- `POST /api/budgets/seed-from-history` — top spend → daily/weekly/monthly plans (`only_if_empty` for onboarding)  
- Home: `budgets`, `budget_reserve`, `safe_to_spend_before_budgets`, `budget_seed_hint`  
- **Can I buy?** (`POST /api/pre-purchase`): cash path uses Safe to spend after reserve; optional `category_id` → `budget_check` / `safe_budget_tight`

## UI

- Full books → **Budgets** (CRUD, **Accept** history suggestions, **Seed from spend history**, **apply cuts**)  
- Simple Home → budget summary + **tappable cut chips** + seed tip when empty + reserve footnote  
- **Can I buy?** → optional budget category; reserve-aware Safe to spend; cut → **auto re-check**  
- **Settings** → reserve on/off, week start, workday checkboxes  

## Settings (app_settings)

- `budget_reserve_enabled`  
- `budget_week_starts_on` (0=Mon)  
- `budget_workdays` (bitmask Mon=bit0 … default 31 = Mon–Fri)  
