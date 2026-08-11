# Simple mode (v0.7)

**North star:** ridiculously powerful engine, stupid-simple daily UI.

## What users see (Simple)

| Surface | Purpose |
|---------|---------|
| **Home** | Safe to spend · status · Do this next · Up next · wealth basics |
| **Home · card pay whisper** | Only when a card **next payment** is due within 14 days: “Next card payments: $X on {date}” (sum same-day). No cycle editor, no day grid |
| **Add** | Wizards: cash, savings, card, loan, bill, income, business, child |
| **Get started** | First-run wizard (~2 min) |
| **Can I buy?** | Pre-purchase check |
| **Sort charges** | Plain review queue (accept categories) |
| **3-minute check** | On Home — open-rarely ritual (safe · charges · fees · promo · bills) |
| **After import / bank** | `books_brief` — **books≠bank** · uncategorized · pending · stale Plaid |
| **Fee check** | `fee_brief` — fee-like charges last 45d |
| **Possible bills** | `recurring_suggestions` — repeat charges not yet scheduled |
| **0% promo** | `promo_brief` — one-tap monthly set-aside |
| **Close the month** | `month_close` — fees · charges · promo · tax · reconcile · backup |
| **Move money** | Add → Playbooks (reimburse · pay myself · fund biz · kid allowance) |

**Full books** (shell toggle) restores the full nav for month-end / CPA / power users — including the **Credit** desk (statement close/due, pay policy, pay-from cash, projected statement balance, next payment, promo installment lines). Simple never shows that desk; cash next payments still feed **Safe to spend** in the background.

See [STATEMENT_CYCLES.md](./STATEMENT_CYCLES.md).

## Language

| Avoid | Say |
|-------|-----|
| IFPP | Safe to spend |
| entity / group | This money / All money |
| profile | Who |
| never_negative_enforcement | Protect checking |
| `min` / `statement` / `promo_sink` | Minimum only / Pay statement in full / 0% promo set-aside |
| `cash` / `card` methods | Cash / checking · Card (interest-free when safe) |
| raw account / txn `#id` | Nickname in pickers and messages |
| projected statement / cycle config | Statement balance · next payment · close day / due day / pay from (Full books only) |

## APIs

- `GET /api/home/simple` — primary Home payload  
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
