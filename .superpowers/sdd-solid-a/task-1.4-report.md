# Task 1.4 Report: Digit-safe card_account_id advance tests

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `1ec48d0` — `test: digit-safe card_account_id advance filter`  
**Date:** 2026-08-11

## Goal

Lock digit-safe filter: schedule notes `card_account_id=1;` must not match account_id=12 in `advance_schedules_after_import`.

## Problem

A naive `LIKE '%card_account_id=1%'` matches notes for `card_account_id=12` because digit `1` is a string prefix of `12`. After import, filtering by the wrong credit account could settle the sibling card's payment schedule when amounts match.

## Production state

**No production change required.** Filter in `advance_schedules_after_import` already uses digit-safe predicates:

```python
(ScheduledItem.account_id == account_id)
| (ScheduledItem.notes.like(f"%card_account_id={account_id};%"))
| (ScheduledItem.notes.endswith(f"card_account_id={account_id}"))
```

`;` after the id (normal notes) or end-of-string (marker-only) prevents prefix collision.

## Tests added

### `tests/test_schedule_mark_paid.py`

1. **`test_advance_digit_safe_card_account_id_prefix`**
   - Explicit credit ids **1** and **12** (prefix-related).
   - Two Card payment schedules, **same amount** (`-150.00`), notes `card_account_id=1;` vs `card_account_id=12;`.
   - Matching cash outflows for both payees on the funding account.
   - `advance_schedules_after_import(account_id=1)` advances **only** Alpha (id=1); Beta next_date unchanged.
   - Then `account_id=12` advances **only** Beta.

2. **`test_advance_digit_safe_card_account_id_does_not_cross_match`**
   - Only schedule tagged `card_account_id=12;` exists (broken filter would select it for `account_id=1`).
   - Cash outflow matches that schedule's payee.
   - Filter `account_id=1` → `advanced_count == 0`, next_date untouched.
   - Filter `account_id=12` → advances once.

Existing same-amount different-payee coverage (`test_mark_paid_same_amount_does_not_steal`) kept.

## Tests run

```
.venv\Scripts\python.exe -m pytest tests/test_schedule_mark_paid.py -q --tb=short
```

**Result:** 20 passed

## Constraints check

| Constraint | OK |
|------------|----|
| Prefix ids 1 vs 12 in notes | yes |
| Same-amount cash only advances tagged account | yes |
| Production only if filter broken | yes (no prod change) |
| Commit message as specified | yes |
| Only this task files | yes |
| No WinUI / unrelated churn | yes |

## Files committed

1. `tests/test_schedule_mark_paid.py`
2. `.superpowers/sdd-solid-a/task-1.4-report.md`
3. `.superpowers/sdd-solid-a/progress.md`

## Notes / follow-ups

- Phase 1 match-hardness locked; engine review can re-grade import advance for A.
- Marker-only notes path (`endswith card_account_id={id}`) covered by production filter; both tests use standard `;`-terminated notes (same as autopay writer).