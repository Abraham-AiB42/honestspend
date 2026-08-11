# Task 2.3 Report: Soft until docs + SIMPLE_MODE language cleanup

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `1a497db` — `docs: soft until post-reserve + Simple language table`  
**Date:** 2026-08-11

## Goal

Lock soft-until product language after Task 1.1 post-reserve base:

1. Soft until = **Left after Coming up bills (same cash as Safe)** (post-reserve).
2. Surface table: engineer "IFPP next risk day" → **Next risk day**.
3. Soft may hide when equal to Safe (WinUI).
4. CHANGELOG Unreleased one-line touch.

## Change

### `docs/SIMPLE_MODE.md`

- Home surface row: `soft until (when distinct) · **Next risk day**` (no IFPP).
- New **Soft until (secondary cash line)** section: product meaning, base, math, useful-when, WinUI hide, labels.
- Cash-side rules: soft = post-reserve − Coming up outflows; hide when same $ as Safe; risk day plain language.
- Language table: "IFPP next risk day" → "Next risk day"; soft meaning → "Left after Coming up bills (same cash as Safe)."

### `docs/STATEMENT_CYCLES.md`

- Cash runway note: Home risk = **Next risk day** (not "IFPP next risk day"); pointer to SIMPLE_MODE for soft until.

### `CHANGELOG.md` (Unreleased)

- Trust bar: soft post-reserve label; risk line = **Next risk day**.
- Cash runway bullet: Next risk day wording.
- Docs bullet: soft until + Simple language table.

No app code (engine already post-reserve via Task 1.1).

## Constraints check

| Constraint | OK |
|------------|----|
| Soft = post-reserve "Left after Coming up bills…" | yes |
| Surface table drops IFPP next risk day | yes |
| Hide when equal to Safe noted | yes |
| CHANGELOG Unreleased updated | yes |
| Docs only (no feature code) | yes |
| Commit message as specified | yes |

## Files committed

1. `docs/SIMPLE_MODE.md`
2. `docs/STATEMENT_CYCLES.md`
3. `CHANGELOG.md`
4. `.superpowers/sdd-solid-a/task-2.3-report.md`
5. `.superpowers/sdd-solid-a/progress.md`

## Notes / follow-ups

- WinUI `ApplySafeUntilWindow` product contract documents hide-when-equal; if UI still shows identical soft, that is a later client polish (not this docs task).
- Task 2.4: shared never-neg 409 helper (WinUI).
