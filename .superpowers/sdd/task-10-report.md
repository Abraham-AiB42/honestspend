# Task 10 Report — Integration dogfood tests (engine)

## Status
**Complete**

## Deliverable
- Created `tests/test_statement_cycle_e2e.py`
- Scenario: `test_card_first_statement_pay_affects_safe`
  - Checking $5000, safety buffer 0
  - Card policy `statement`, funding checking, due ~5 days out, balance 0
  - Charge $800 → `next_payment` 800, cash schedule −800 on due date
  - `run_ifpp` → `cash_spendable` reduced by at least one full payment (horizon may project 2 monthly dues)
  - Promo line remaining 300 / monthly 50 → payment = 800 − 300 + 50 = **550**; Safe rises vs full-statement path

## Commit
`test: statement cycle e2e card-first Safe to spend`

## Suite results
```
pytest tests/test_statement_cycle.py tests/test_account_ledger.py tests/test_autopay_recompute.py tests/test_promo_installments.py tests/test_statement_cycle_e2e.py tests/test_ifpp_credit_bill_schedule.py tests/test_statement_cycle_schema.py tests/test_statement_cycle_api.py tests/test_cycle_config_learn.py -q
```
**60 passed**, 1 warning (Starlette/httpx TestClient deprecation)

## Concerns
- Default IFPP horizon (45d) + monthly `Card payment` cadence can reserve **two** dues (e.g. 5000 − 1600 = 3400 for an $800 payment). Assertions use relative with/without schedule rather than a single absolute spendable.
- Promo recompute is explicit (`recompute_card_payment_schedule` after `create_promo_line`); create path does not auto-hook schedule refresh (matches existing unit tests).

## Out of scope (unchanged)
Excel day-grid UI, PDF cycle close, auto-pay-the-bank, Simple mode credit desk.

## Final review fix

**Commit:** `fix: recompute after plaid/trust/rebuild; end schedules on archive`

Addressed Important whole-branch review findings: credit books paths that skipped schedule recompute, and archive leaving cash Card payment schedules active.

### Hooks
1. **Plaid** (`plaid_service._sync_accounts`) — After a credit `current_balance` snapshot is applied (existing or new), call `after_account_balance_changed` for each touched credit account (de-duped at end of pass).
2. **reconcile.trust_balance** — When `trust=institution` changes books and account is credit, call `after_account_balance_changed`.
3. **account_ledger.rebuild_running_balance** — After writing credit books (and available_credit), call `after_account_balance_changed`.
4. **Archive credit** (`POST /api/accounts/{id}/archive`) — For `kind=credit`, call `_end_card_payment_schedules` and clear statement/next payment caches.

### Tests
- `test_rebuild_credit_triggers_card_payment_recompute` — rebuild restores books + schedule amount/caches
- `test_trust_institution_recomputes_card_payment_schedule` — trust institution updates schedule
- `test_archive_credit_ends_card_payment_schedule` — archive ends cash Card payment schedule + clears caches

### Verify
```
pytest tests/test_account_ledger.py tests/test_autopay_recompute.py tests/test_statement_cycle_e2e.py tests/test_reconcile.py -q
```
**19 passed**
