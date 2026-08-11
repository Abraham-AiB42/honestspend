# Task 1.2 Report: autopay_policy sole pay-policy authority

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** (see git log) — `refactor: autopay_policy is sole pay-policy authority`  
**Date:** 2026-08-11

## Goal

`autopay_policy` is the only field that determines credit **what to pay** amounts. `payment_option` remains a **deprecated wizard alias** (write-through map only) for one release — no amount logic reads `payment_option` except to backfill empty policy.

## Authority model

| Path | Behavior |
|------|----------|
| Amount / schedule math | `autopay_policy` only (via `ensure_autopay_policy_from_payment_option` when null/blank) |
| Explicit `none` | Sticky — never backfilled from `payment_option` |
| Wizard `payment_option` write | Maps → sets `autopay_policy` first, then mirrors alias |
| Policy write (cycle PUT / set_autopay) | Sets policy; reverse-sync mirrors alias for UI (not for amounts) |
| Column drop | Not done (one-release alias retained) |

## Changes

### `src/financial_os/services/autopay.py`

- Documented `ensure_autopay_policy_from_payment_option` as sole amount authority (sticky `none`).
- `sync_payment_option_from_policy` — display mirror only; `none` leaves alias unchanged.
- `list_autopay` uses `ensure_*` so suggested amounts match recompute.
- `set_autopay` reverse-syncs alias after writing policy.
- `recompute_card_payment_schedule` continues to call `ensure_*` before projection.

### `src/financial_os/services/statement_cycle.py`

- `project_card_payment` calls `ensure_autopay_policy_from_payment_option` so GET cycle / freeze / list paths cannot show amounts from a blank policy while alias is set.

### `src/financial_os/services/setup_discover.py`

- Dropped local `_AUTOPAY_MAP`; uses shared `PAYMENT_OPTION_TO_POLICY`.
- Discover apply: write `autopay_policy` first, `payment_option` as deprecated alias.
- `set_account_payment_option`: thin policy-first write-through; docstring marks deprecated edge.

### `src/financial_os/api/app.py`

- `_apply_payment_option_alias` on account create/update/patch (before cycle defaults on create).
- `PUT …/cycle-config`: reverse-sync alias when policy written.

### `src/financial_os/db.py`

- Column comments: policy = sole authority; `payment_option` = deprecated alias.

### Docs

- `docs/STATEMENT_CYCLES.md` — pay policy row: `autopay_policy` sole what-to-pay field.
- `docs/SIMPLE_MODE.md` — Full books Credit desk: pay policy = `autopay_policy`.

### Tests

- Reverse-sync assertions (set_autopay, cycle PUT min → `payment_option=minimum`).
- Sticky `none` through charge / recompute.
- Discover default (no option) → `autopay_policy=statement`.
- `set_account_payment_option` fixed → policy `fixed`.

## Grep: remaining `payment_option` reads (services + api)

| Location | Why OK |
|----------|--------|
| `autopay.ensure_autopay_policy_from_payment_option` | Edge backfill only when policy null/blank |
| `autopay.sync_payment_option_from_policy` | **Write** alias for display; not amount read |
| `setup_discover.apply_discoveries` | Wizard **write** of alias after policy |
| `setup_discover.set_account_payment_option` | Deprecated wizard write-through |
| `api.AccountIn` / `AccountPatch` / `PaymentOptionIn` | Accept alias payload |
| `api._apply_payment_option_alias` | Map write → policy |
| `api.account_payment_option` | Wizard POST → set_account_payment_option |
| `api.put_account_cycle_config` | Reverse-sync **write** |
| `db.Account.payment_option` | Column (no logic) |
| `migrations._mig_15_*` | Historical schema only |

**No amount path** (min/statement/fixed/promo_sink/books) reads `payment_option` for the decision itself.

## Tests run

```
.venv\Scripts\python.exe -m pytest tests/test_autopay_recompute.py tests/test_setup_discover.py tests/test_statement_cycle_api.py tests/test_cycle_config_learn.py -q --tb=short
```

**Result:** 40 passed

| Suite | Notes |
|-------|--------|
| `test_autopay_recompute.py` | reverse-sync, sticky none, recompute |
| `test_setup_discover.py` | alias map + default statement policy |
| `test_statement_cycle_api.py` | PUT min → policy min + alias minimum |
| `test_cycle_config_learn.py` | discover → statement policy |

## Constraints check

| Constraint | OK |
|------------|----|
| Amounts use `autopay_policy` only | yes |
| `payment_option` alias write-through at wizard edges | yes |
| Sticky `none` preserved | yes |
| No DB column drop | yes |
| No WinUI Credit structure change | yes |
| Only task files committed | yes |

## Files committed

1. `src/financial_os/services/autopay.py`
2. `src/financial_os/services/setup_discover.py`
3. `src/financial_os/services/statement_cycle.py`
4. `src/financial_os/api/app.py`
5. `src/financial_os/db.py`
6. `tests/test_autopay_recompute.py`
7. `tests/test_setup_discover.py`
8. `tests/test_statement_cycle_api.py`
9. `docs/STATEMENT_CYCLES.md`
10. `docs/SIMPLE_MODE.md`
11. `.superpowers/sdd-solid-a/task-1.2-report.md` (this file; may be untracked until add)

## Notes / follow-ups

- Drop `payment_option` column after one release when clients stop sending it.
- WinUI still may show wizard strings; map stays in `PAYMENT_OPTION_TO_POLICY` / `POLICY_TO_PAYMENT_OPTION`.
- Task 1.3 is void/recompute symmetry (separate).
