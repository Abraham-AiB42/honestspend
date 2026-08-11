# Task 1.3 Report: Void/reverse recompute symmetry for card payments

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `ef8927a` — `fix: void card payment recompute once cleanly`  
**Date:** 2026-08-11

## Goal

After void of a card payment (transfer pair from mark-paid), `Card payment ·` schedule recomputes **once** to a consistent next_date/amount — not double-step garbage.

## Problem

| Path | Before | After |
|------|--------|-------|
| Mark-paid new card leg | `recompute=False` mid-flight, then one restore + amount refresh | unchanged (task 1.x mark-paid work) |
| Void **card** leg | `reverse_amount_on_account` recomputed (OK once) | Still once; deferred until after `status=void` |
| Void **cash** leg of transfer pair | No credit reverse → **no recompute** → stale next_date/amount | Recompute card schedule once via pair |
| Void both legs | Card leg recomputed; cash leg noop for schedule | Each void triggers full recompute (idempotent) |

Cash-void gap: schedule could remain at mark-paid advanced date or a corrupted double-step while cash books were undone.

## Changes

### `src/financial_os/services/account_balance.py`

- `reverse_amount_on_account(..., recompute: bool = True)` — symmetry with `apply_amount_to_account`.
- Credit recompute skipped when `recompute=False` so void can own a single post-status recompute.

### `src/financial_os/services/txn_void.py`

- `_credit_ids_for_void_recompute`: credit account on the voided row **and/or** transfer-pair credit account (cash leg of mark-paid card payment).
- Reverse with `recompute=False` + `session=`.
- After `status=void` flush, call `recompute_card_payment_schedule` **once per** related credit id.
- Response may include `recomputed_card_account_ids`.

### Tests: `tests/test_void_card_payment_schedule.py`

1. Mark paid card new legs → next_date = original + 1 month.
2. Void card leg → amount/due match honest recompute; not past one extra month; balance restored.
3. Void cash leg with **corrupted** schedule (Oct double-step / $1) → rewritten once via pair recompute.
4. Void both legs → books restored; schedule matches honest recompute.

## Tests run

```
.venv\Scripts\python.exe -m pytest tests/test_schedule_mark_paid.py tests/test_void*.py tests/test_wave2_books.py -q --tb=short
```

**Result:** 27 passed

| Suite | Notes |
|-------|--------|
| `test_void_card_payment_schedule.py` | 4 new symmetry tests |
| `test_schedule_mark_paid.py` | mark-paid single advance regression |
| `test_wave2_books.py` | existing void / transfer match |

## Constraints check

| Constraint | OK |
|------------|----|
| Recompute once on void of card payment (full rewrite OK) | yes |
| Not double-step past honest recompute | yes |
| Cash or card leg void covered | yes |
| Only task files committed | yes |
| No WinUI / unrelated churn | yes |

## Files committed

1. `src/financial_os/services/account_balance.py`
2. `src/financial_os/services/txn_void.py`
3. `tests/test_void_card_payment_schedule.py`
4. `.superpowers/sdd-solid-a/task-1.3-report.md`

## Notes / follow-ups

- Void still does **not** auto-void the transfer pair; only recomputes schedule. Pair books remain until the other leg is voided.
- Patch-txn reverse still uses default `recompute=True` (unchanged callers).
- Task 1.4: digit-safe `card_account_id` match tests (separate).
