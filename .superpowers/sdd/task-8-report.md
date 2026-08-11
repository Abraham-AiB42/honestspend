# Task 8 Report: WinUI — Statement cycle + live card payment UI

## Status
**Complete**

## Commit
`a4a9b3b` — `feat(winui): statement cycle and live card payment UI`

## What shipped

### LedgerApiClient
- `GetAccountCyclesAsync` → `GET /api/accounts/cycles`
- `GetAccountCycleAsync` → `GET /api/accounts/{id}/cycle`
- `PutAccountCycleConfigAsync` → `PUT /api/accounts/{id}/cycle-config`
- `RecomputeAccountCycleAsync` → `POST /api/accounts/{id}/recompute-cycle`
- `GetPromoLinesAsync` / `CreatePromoLineAsync` / `RollPromoLineAsync`

### CreditPage — “Statement & payment”
Per card: Close day · Due day · Policy (incl. fixed) · Pay from (cash picker) · **Projected statement** · **Next payment** · Save settings · Recompute  
Promo/installment lines: list remaining + monthly; add plan (name, remaining, monthly).  
Plain language only (no IFPP). Autopay kept as “quick” policy-only.

### HomePage (optional urgency)
`CardPayWhisper` under float whisper: **“Next card payments: $X on {date}”** when due within 14 days (sums same-day payments).

### AccountsPage (compact)
Credit rows: close day + cached next pay / statement; edit dialog includes statement close day.

### UiCopy
`fixed` → “Fixed amount”

## Build
`dotnet build -c Release -p:Platform=x64` — **succeeded** (0 warnings, 0 errors).

## Files (commit only)
- `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- `clients/HonestSpend.WinUI/Helpers/UiCopy.cs`
- `clients/HonestSpend.WinUI/Pages/CreditPage.xaml(.cs)`
- `clients/HonestSpend.WinUI/Pages/HomePage.xaml(.cs)` — CardPayWhisper only
- `clients/HonestSpend.WinUI/Pages/AccountsPage.xaml(.cs)`

Unrelated dirty store tiles / packaging / other HomePage WIP **not** committed.

## Manual dogfood (not run in-agent)
Still recommended: Amazon-like close 18 / due 15 / statement / funding checking; post charges; confirm next payment rises and Safe to spend reflects cash payment schedule.

## Concerns / follow-ups
- Promo line **edit** remaining/monthly not in UI (add + list only; API has create + roll, no PATCH). Roll not exposed as button yet.
- Home whisper uses 14-day urgency window; no payments → collapsed (correct).
- `payment_funding_account_id: null` on save when no cash selected may clear funding — intentional for “no cash selected”.

## Fix review

### Commit
`fix(winui): cycle funding clear, caches, promo edit`

### Priority fixes
1. **Funding null clear** — `PutAccountCycleConfigAsync` serializes with `JsonIgnoreCondition.Never` so `payment_funding_account_id: null` is sent and clears server funding.
2. **AccountOut caches** — `AccountOut` now includes `statement_balance_cached`, `next_payment_amount_cached`, `next_payment_date_cached`, plus funding/policy/source so AccountsPage next-pay meta works from `GET /api/accounts`.
3. **Accounts close/due** — Edit save on credit calls `PutAccountCycleConfigAsync` for close/due so schedules recompute.
4. **Promo edit** — `PATCH /api/accounts/{id}/promo-lines/{line_id}` + `update_promo_line`; CreditPage plan picker, Save plan changes, Roll one month.
5. **Autopay Fixed** — Removed Fixed from quick Autopay combo; copy points to Statement & payment for fixed amount.

### Build / tests
- `dotnet build -c Release -p:Platform=x64` — succeeded (0/0).
- pytest: `test_api_promo_line_patch`, `test_put_cycle_config_clears_funding_with_null`, `test_list_accounts_includes_cycle_caches` — passed.

### Files (this fix commit only)
- `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- `clients/HonestSpend.WinUI/Pages/AccountsPage.xaml.cs`
- `clients/HonestSpend.WinUI/Pages/CreditPage.xaml(.cs)`
- `src/financial_os/api/app.py`
- `src/financial_os/services/promo_installments.py`
- `tests/test_promo_installments.py`
- `tests/test_statement_cycle_api.py`
- `.superpowers/sdd/task-8-report.md`
