# Final whole-branch review fixes (smart-charge)

**Status:** Complete  
**Branch:** `main`  
**Commit:** `f00094a` — `fix(smart-charge): D10 fingerprints, user-source edits, fail-closed ranker`

## Must-fix

| ID | Change | Result |
|----|--------|--------|
| C1 | Writers persist `promo_fingerprint`; default intro identity is `__intro__` (empty tail); upsert also matches null-fingerprint by account+kind+norm name; statement may fill empty end/APR | Tests below fail→pass |
| C2 | `update_promo_line` / `PromoLinePatch` accept `end_date`; editor save always `source=user`; WinUI persists `EditorEndBox` | Fail→pass |
| I3 | `_ranker_safe` except → `(False, {})` so verdict is skip | Fail→pass |
| I4 | `_do_this_next` keeps `promo_conflict` (does not rewrite to `promo_sink`); Home `DoNext_Click` navigates Offers | Fail→pass |
| I5 | `_insert_conflict` reuses unresolved same line_id+incoming_source; ImportPage scopes `GetPromoConflictsAsync(accountId)` | Fail→pass |
| I7 | Import conflict dialog: Primary Keep, Secondary Take, content Edit; Close/Esc leaves unresolved | UI change (no pytest) |

## Tests (TDD)

First run (before impl): **10 failed** (new behaviors).

After impl:

```
pytest tests/test_promo_upsert.py tests/test_promo_installments.py \
  tests/test_offers.py tests/test_home_simple.py tests/test_plaid_promo_upsert.py \
  tests/test_promo_statement_apply.py tests/test_promo_statement_parse.py \
  tests/test_pre_purchase_commit.py tests/test_promo_float_from_lines.py \
  tests/test_promo_calendar.py tests/test_promo_term_schema.py -q
→ 71 passed, 1 warning (Starlette/httpx TestClient deprecation)
```

New tests: fingerprint `__intro__` collision; create_promo_line no-fp + statement conflict; unsourced name match; statement intro then Plaid special → one `card_intro`; statement fills empty end/APR; user PATCH remaining → source=user then scrape conflicts; conflict dedupe; account-scoped list; ranker raise → skip; home keeps `promo_conflict`; API PATCH `end_date`.

## Remaining concerns

- What-if / `ifpp_simulate` account_id + promo still skipped (Important 6).
- Skip-after-take unwind not touched.
- Import Close/Esc is WinUI-only (no automated test).
- Named card intros (not Intro 0%) still include `end_date` in the fingerprint.
- Leftover 1.0.57 Store/Python/WinUI tree left unstaged.

---

## Follow-up (2026-08-12) — active patch + take_incoming None

**Status:** Complete  
**Branch:** `main`

### Fixes

| ID | Change | Result |
|----|--------|--------|
| F1 | Restore `if active is not None: line.active = bool(active)` in `update_promo_line` so `PromoLinePatch.active=false` deactivates without rem=0 | Fail→pass |
| F2 | `resolve_promo_conflict` `take_incoming`: incoming `end_date`/`apr` None means unknown — keep existing (same rule as `_fields_differ` / silent apply); remaining/monthly still apply | Fail→pass |

### Tests (TDD)

First run (before impl): **3 failed**  
- `test_update_promo_line_active_false_without_zero_remaining`  
- `test_api_promo_line_patch_active_false`  
- `test_take_incoming_none_end_date_keeps_statement_end`

After impl:

```
pytest tests/test_promo_installments.py tests/test_promo_upsert.py \
  tests/test_offers.py tests/test_plaid_promo_upsert.py \
  tests/test_promo_statement_apply.py tests/test_promo_float_from_lines.py -q
→ 51 passed, 1 warning
```

### Concerns

- Explicit clear of end/APR via take_incoming is still impossible when payload sends null (by design: null = unknown).
- 1.0.57 leftovers remain unstaged.
