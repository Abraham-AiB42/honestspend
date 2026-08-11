# Task 6 Report: Promo installment lines (ISB-class)

## Status
**Complete**

## Commit
`feat: promo installment lines (ISB-class)`

## What shipped

### Schema (v18)
- Model: `PromoInstallmentLine` in `src/financial_os/db.py`
- Migration `_mig_18_promo_installment_lines` → table `promo_installment_lines`
- Fields: `account_id`, `name`, `principal_remaining`, `monthly_payment`, `start_date`, `end_date?`, `active`, `source`
- `SCHEMA_VERSION = 18`

### Service
- New: `src/financial_os/services/promo_installments.py`
  - `open_promo_totals(session, account_id, *, as_of) -> (principal_remaining, monthly_due)`
  - `apply_month_roll(session, account_id, *, as_of)` — subtract monthly, deactivate at zero
  - `roll_line`, `create_promo_line`, `list_promo_lines`, `line_to_dict` helpers
- `project_card_payment` now calls `open_promo_totals` (stub removed)
- Statement math: `statement_balance = max(0, bal - promo_remaining)`; policy `statement` payment = carve-out + monthly due

### API
- `GET  /api/accounts/{id}/promo-lines`
- `POST /api/accounts/{id}/promo-lines`
- `POST /api/accounts/{id}/promo-lines/{line_id}/roll`

## Tests
`tests/test_promo_installments.py` — 7 tests:
- Schema v18 + table columns
- Open totals + month roll on 823.78 / 12 (monthly 68.65 → remaining 755.13)
- Statement carve-out: bal 5000 → statement 4176.22, payment 4244.87
- Deactivate when principal hits zero
- Future start_date excluded
- API list/create/roll + cycle projection
- 404 on missing line

Also green: `test_statement_cycle.py`, `test_migrations.py` (14 total in verification run).

## Files committed
- `src/financial_os/db.py`
- `src/financial_os/migrations.py`
- `src/financial_os/services/promo_installments.py` (new)
- `src/financial_os/services/statement_cycle.py`
- `src/financial_os/api/app.py`
- `tests/test_promo_installments.py` (new)

## Concerns / follow-ups
- Roll is manual (API per-line); not yet automatic on statement cycle close — Task 8 UI / later cycle-close hook may want `apply_month_roll` on close.
- No PATCH/DELETE for lines yet (create + roll only).
- `project_card_payment` now returns `promo_remaining` / `promo_due`; cycle API serializer does not surface them yet (optional polish).

## Fix review

Addressed Important Task 6 review findings:

1. **Cap monthly due** — `open_promo_totals` now adds `min(principal_remaining, monthly_payment)` per open line so a partial final installment is not overstated.
2. **Recompute after create/roll** — POST promo-line create and POST roll call `recompute_card_payment_schedule` so Account payment caches + cash schedule refresh.
3. **Roll window** — `apply_month_roll` already skipped non-open lines; `roll_line` now raises a clear `ValueError` when forced on not-yet-open / past-end; API maps that to HTTP 400.

Tests added: partial final installment cap, apply_month_roll skip future start, roll_line not-open error, API recompute cache after create/roll, API 400 on roll not-open.

Verify: `pytest tests/test_promo_installments.py tests/test_statement_cycle.py -q` → 18 passed.

Commit: `fix: cap promo due; recompute after promo create/roll`
