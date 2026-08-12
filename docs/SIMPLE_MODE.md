# Simple mode (v0.7)

**North star:** ridiculously powerful engine, stupid-simple daily UI.

## What users see (Simple)

| Surface | Purpose |
|---------|---------|
| **Home** | Safe to spend · soft until (when distinct) · **Next risk day** · status · Do this next · **Coming up** · wealth basics — no busy-day strip / day grid |
| **Home · Coming up** | Cash-side bills / card payments / optional paychecks until **N calendar days (7–14)** or the **next 1–2 paydays** (auto when income known; Settings). Totals cover the **full window**; list shows top rows. Card payment on checking yes; Netflix on card no. **Owner draw** = outflow (never a payday). Payroll package legs (net + employer tax) both appear on pay day. Empty: “Nothing scheduled in this window — add a bill or paycheck in Add” |
| **Home · card pay whisper** | Only when a card **next payment** is due within 14 days: “Next card payments: $X on {date}” (sum same-day). Setup asks **due day + pay-from checking** only (statement in full). Close day / timing / books-balance stay on Full books |
| **Add** | Wizards: cash, savings, card, loan, bill, income, business, child (bill: pay from · owner draw · amount steps — see [BUSINESS_ENTITY.md](./BUSINESS_ENTITY.md); payroll day lives on Full books **Bills**) |
| **Get started** | First-run wizard (~2 min) |
| **Can I buy?** | Pre-purchase check — **smart_charge** ranks interest-free float, then rewards (safe card can beat cash) |
| **Sort charges** | Plain review queue (accept categories) |
| **3-minute check** | On Home — open-rarely ritual (safe · charges · fees · promo · bills) |
| **After import / bank** | `books_brief` — **books≠bank** · uncategorized · pending · stale Plaid |
| **Fee check** | `fee_brief` — fee-like charges last 45d |
| **Possible bills** | `recurring_suggestions` — repeat charges not yet scheduled |
| **0% promo** | `promo_brief` — one-tap monthly set-aside |
| **Close the month** | `month_close` — fees · charges · promo · tax · reconcile · backup |
| **Move money** | Add → Playbooks (reimburse · pay myself · fund biz · kid allowance) |

**Full books** (shell toggle) restores the full nav for month-end / CPA / power users — including the **Credit** desk (statement close/due, pay policy = `autopay_policy`, pay-from cash, projected statement balance, next payment, promo installment lines). Simple never shows that desk; cash next payments still feed **Safe to spend** in the background.

See [STATEMENT_CYCLES.md](./STATEMENT_CYCLES.md).

## Soft until (secondary cash line)

**Product meaning:** **Left after Coming up bills (same cash as Safe).**

| Rule | Detail |
|------|--------|
| **Base** | Runway cash after buffer/tax vault, **minus budget reserve** — **not** Safe itself (Safe already reserved schedule bills over the horizon) |
| **Math** | `soft_start = runway_start − budget_reserve`; `soft = max(0, soft_start − Coming up outflow total)`; never above Safe (`cap_at` = Safe) |
| **Why not Safe − outflows** | That **double-counts** window bills already inside Safe — soft would understate residual |
| **Useful when** | Soft sits **below** Safe (window bills or buffer path differs) and teaches “after bills in this Coming up window” |
| **WinUI hide** | When soft **equals** Safe (same $), Home **hides** the soft line so two identical numbers do not compete |
| **Labels** | Plain English window end (“Until payday…”, “Until Fri Mar 6…”) — no “horizon”, no IFPP |

Soft is secondary; **Safe to spend** stays the only primary cash number.

## Coming up strip

Plain list on Simple Home: bills, card cash payments, and optional paychecks through a short window — not a day grid. (`GET /api/cash-runway` exists for Full/tools; Home does not show a runway busy-day strip.)

### Window modes

| Mode | End date | Default |
|------|----------|---------|
| **auto** | If ≥1 active income schedule in scope → **paydays** (N=1); else **calendar** 14 days | Yes |
| **calendar** | `today + calendar_days` | Days **7–14** (default **14**) |
| **paydays** | Date of the **Nth** future paycheck (N **1 or 2**, default **1**) | Cap: never past **today + 45** days (`window_capped`) |

- No income + mode `paydays` → fall back to calendar 14 (`window_fallback: no_income`); subtitle may say “no paycheck scheduled”.
- Subtitle examples: `Next 14 days` · `Until Fri Mar 6 (next payday)` · `Until … (2nd payday)`.

### Cash-side rule (what appears)

| Include | Skip |
|---------|------|
| Active schedules on **cash** (checking/savings) in the window | Schedules on **credit** accounts (e.g. Netflix on the card) — same skip as Safe to spend |
| **`Card payment · {nickname}`** on the pay-from checking account | Engine jargon (“horizon”, IFPP, “runway”, “busy days”) |
| **Safe to spend** as the only primary cash number | Competing “runway starts $X” on the risk line |
| **Soft until** = runway − budgets − Coming up outflows (once), capped at Safe | Soft = Safe − outflows again (double-count); soft &gt; Safe |
| **Next risk day** from the same engine path as Safe | Overwriting risk with a second runway call / engineer “IFPP” in UI copy |
| **Import matches bills** (CSV/OFX/Plaid/PDF, high confidence) → advance schedule | Bank paid Rent but Coming up still reserves it |
| **Mark paid never-neg confirm** on Home (and Full Bills) when warn mode | Silent 409 with no way to finish a real payment |
| Soft until **hidden when same $ as Safe** (WinUI) | Two identical cash numbers fighting for attention |
| Coming up / soft labels: **plain English** (“next few weeks”) | Engine jargon (“horizon cap”, “IFPP”) |
| Optional **income** rows (default on; `coming_up_show_income`) | — |
| **Owner draw** (cash) as outflow | Owner draw never counts as paycheck/income |
| **Payroll package** legs (`· net` + `· employer tax`) on the same pay date | Full payroll/HR (not in product — see [BUSINESS_ENTITY.md](./BUSINESS_ENTITY.md)) |

Sort by date, then larger |amount|. Simple shows top **8** rows.

### Settings & API

| Pref (`AppSettings`) | Values | Default |
|----------------------|--------|---------|
| `coming_up_window_mode` | `auto` · `calendar` · `paydays` | `auto` |
| `coming_up_calendar_days` | 7–14 | 14 |
| `coming_up_payday_count` | 1–2 | 1 |
| `coming_up_show_income` | bool | true |

- Embedded in `GET /api/home/simple` as `coming_up`
- Standalone: `GET /api/coming-up` (query overrides: `mode`, `calendar_days`, `payday_count`, `show_income`, `limit`)
- Persist: `PATCH /api/settings` with `coming_up_*` (WinUI **Settings → Coming up**)

## Language

| Avoid | Say |
|-------|-----|
| IFPP | Safe to spend |
| IFPP next risk day | **Next risk day** |
| Soft until / Safe until (jargon-free meaning) | **Left after Coming up bills in this window** (cash after buffer, tax vault, and budgets — not Safe minus those bills twice) |
| entity / group | This money / All money |
| profile | Who |
| never_negative_enforcement | Protect checking |
| `min` / `statement` / `promo_sink` | Minimum only / Pay statement in full / 0% promo set-aside |
| `cash` / `card` methods | Cash / checking · Card (interest-free when safe) |
| raw account / txn `#id` | Nickname in pickers and messages |
| projected statement / cycle config | Statement balance · next payment · close day / due day / pay from (Full books only) |

## APIs

- `GET /api/home/simple` — primary Home payload (includes `coming_up`)  
- `GET /api/coming-up` — Coming up strip (window + cash-side items)  
- `POST /api/onboarding/first-run` — atomic first-run (cash + optional card + bill)  
- Existing account/scheduled/profile endpoints for wizards  

## Wealth tips

Only after safety. Educational only: employer match → IRA habit → 529 (if kids) → optional IUL education.  
**Not investment advice.**

## Do this next priority (Simple)

1. Protect checking / red day / rescue  
2. **Books ≠ bank** — Set Safe to spend from bank ending bal (honesty)  
3. Fees · promo balloon · high-APR debt  
4. Sort charges (also secondary when honesty is primary)  
5. Tax set-aside **only** if business entity or tax rate set (self-employed)  
6. Wealth basics (when safe)  
7. Otherwise: **You're clear — open rarely**  

Empty tax vault for a plain personal / W-2 setup is **optional**, never the day-one nag.
