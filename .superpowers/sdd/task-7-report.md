# Task 7 Report: Import / Plaid config hints (non-destructive)

## Status
**Complete**

## Commit
`feat: cycle config defaults and import-safe learning`

## What shipped

### Service
- New: `src/financial_os/services/cycle_config.py`
  - `apply_credit_cycle_defaults` — fill-missing close/due/funding/policy
  - **Hard freeze:** if `cycle_config_source == "user"`, never auto-change those fields
  - Defaults: close=1, due=15, policy=`statement`, funding = first checking (else cash-like)
  - `suggest_funding_from_payment_history` — optional “Payment to {card}” on cash
  - `default_funding_account_id` — checking preferred over savings

### Wired create / import paths
| Path | Behavior |
|------|----------|
| `setup_discover.apply_discoveries` (credit) | Body close/due if present; funding + missing cycle via helper; `source=import` |
| `plaid_service._sync_accounts` | New + existing credit: fill-missing only; `source=plaid` |
| `bank_csv.import_bank_csv` | After import into credit: fill-missing; `source=import` |
| `onboarding.apply_quick_setup` (card) | Body close/due; funding cash; `source=default` |
| `POST /api/accounts` | Credit creates get defaults; body close/due honored |

## Tests
`tests/test_cycle_config_learn.py` — user freeze; import defaults; body override; fill-missing only; setup_discover funding; plaid new + user-locked; payment-history funding; checking preference; bank_csv fill.

Related suites still green: setup_discover, onboarding, plaid_service, bank_csv, statement_cycle, statement_cycle_api, autopay_recompute.

## Files
- `src/financial_os/services/cycle_config.py` (new)
- `src/financial_os/services/setup_discover.py`
- `src/financial_os/services/plaid_service.py`
- `src/financial_os/services/bank_csv.py`
- `src/financial_os/services/onboarding.py`
- `src/financial_os/api/app.py`
- `tests/test_cycle_config_learn.py`

## Concerns / follow-ups
- Discover still defaults **payment_option** to `interest_saving` (promo_sink) when the user accepts that path; cycle helper only fills blank `autopay_policy` (statement). Intentional product recommendation, not a freeze violation.
- OFX import path not wired for cycle fill (CSV + Plaid + discover covered); easy follow-up if needed.
- Payment-history funding uses nickname token match; may miss heavily abbreviated payees.

## Fix review

**Commit:** `fix: cycle learn — sticky select UX; payment-like funding match`

### Important
1. **Restored prior discover `selected` UX (out of scope for T7):** Reverted credit/loan auto-select to mid conf (`≥0.65`) and merge-in recurring bills to `selected=True`. Kept only cycle-config defaults/funding wiring on discover apply.
2. **Tightened `suggest_funding_from_payment_history`:** Require payment/transfer-like payee phrasing (`_PAYMENT_TO`: payment to / pymt / transfer to / ACH pmt / etc.) **and** card-name match. Card name alone no longer counts as a funding signal.
   - Regression: `test_suggest_funding_ignores_card_name_without_payment_phrasing`
   - Existing `test_suggest_funding_from_payment_to_card` still green

### Tests
`pytest tests/test_cycle_config_learn.py tests/test_setup_discover.py -q` — 14 passed.
