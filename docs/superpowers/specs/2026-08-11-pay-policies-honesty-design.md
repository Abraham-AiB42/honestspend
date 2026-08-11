# Pay policies + honesty (Slice 1)

**Status:** Approved for implementation  
**Date:** 2026-08-11  
**Product:** Everyone (not personal Excel one-offs)

## Intent

Support real payment styles people use—especially **pay current books balance on statement close day or the day before** so the statement can generate near **$0**—with **plain-language credit education**, **interest/min honesty**, and a **utilization desk**. Food/gas “pads” stay **period budgets**, not credit payment fudge.

## Out of scope (later slices)

- Cash runway day strip (30–45d calendar)
- Promo 12‑month amortization calendar UI
- Statement actual freeze from import
- Rewards category chooser
- Personal one-off ledgers (business haul, commissions, medical claims, decade payroll)

## Model

### Amount policy (`autopay_policy`)

| Value | Next payment amount |
|-------|---------------------|
| `none` | 0 (no cash schedule) |
| `min` | Issuer `min_payment` or max(25, 2% × books) |
| `statement` | max(0, books − promo_remaining) + promo_due |
| `promo_sink` | promo_due only |
| `fixed` | `payment_fixed_amount` |
| **`books`** | **max(0, books)** — full current balance including promo principal |

**`books` vs promo:** Full balance is intentional (zero-statement strategy). If open promo lines exist, UI/API returns a **warning** that this may end 0% float early; suggest switching to `statement` + `on_due` to keep installments.

### Pay timing (`payment_timing`) — new Account field

| Value | Schedule date |
|-------|----------------|
| `on_due` (default / null) | Next `payment_due_day` (today’s behavior) |
| `on_close` | Next `statement_close_day` strictly after as_of (or on as_of if same day rules: use next close ≥ as_of for “pay today if close today” — **v1: next close ≥ as_of** when as_of is close day, else next_close) |
| `day_before_close` | Calendar day before next close (if close is 1st → last day of prior month) |

Missing close day + close-based timing → fall back to `on_due` and tip: set close day.

### Cash schedule

Unchanged shape: `Card payment · {nickname}` on pay-from cash, amount `−next_payment`, date from timing, recompute on books change.

### Utilization desk

- Util % = books / limit when limit > 0  
- Amount to hit target T% = max(0, books − limit × T/100)  
- Targets: app settings soft/hard (default 10 / 30)  
- Action: set fixed amount to “amount to target” (or one-shot recompute fixed) — optional API helper

### Interest / min honesty

- Min: issuer min or estimated floor  
- Est. monthly interest if min-only: simple (APR/12) × books, labeled estimate  
- Comparison payload for UI

### Credit advice (educational only)

When policy is `books` with close timing (and available for all policies via “How credit reporting works”):

- Many issuers report near statement close; paying down before can lower reported utilization  
- Very low util often helps; all cards at $0 is not required  
- Early pay reduces interest risk but shortens cash float; Safe to spend reserves cash earlier  
- We do **not** compute FICO/Vantage for this feature  

Copy: estimate / often / many issuers — never “your score will.”

## API

- `payment_timing` on cycle-config PUT and AccountOut / cycle GET  
- Cycle projection includes: `payment_timing`, `next_due` (effective pay date), `warnings[]`, `honesty` block (min, est_interest_min, util_pct, amount_to_soft, amount_to_hard, advice[])  
- Policies list includes `books`

## UI (Full books Credit)

- Policy picker includes **Pay current balance**  
- Timing: Due day · Day before statement closes · On statement close day  
- Honesty panel: min, est interest, util, pay-to-target chips, expandable credit advice  
- Simple mode: unchanged whisper only (no new desk)

## Constraints

- Local-first; never-neg at payment time not schedule upsert  
- Never IFPP in UI  
- TDD; schema via next migration integer  
- User cycle config still sticky  

## Success

1. Card with books $800, close day, timing day_before_close → schedule −800 on day before close  
2. Charge +100 → schedule becomes −900 after recompute  
3. Open promo + books policy → warning present; payment still full books  
4. Util desk math matches formula  
5. Docs describe policies without bureau claims  
