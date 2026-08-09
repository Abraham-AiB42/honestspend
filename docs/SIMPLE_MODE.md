# Simple mode (v0.7)

**North star:** ridiculously powerful engine, stupid-simple daily UI.

## What users see (Simple)

| Surface | Purpose |
|---------|---------|
| **Home** | Safe to spend · status · Do this next · Up next · wealth basics |
| **Add** | Wizards: cash, savings, card, loan, bill, income, business, child |
| **Get started** | First-run wizard (~2 min) |
| **Can I buy?** | Pre-purchase check |
| **Sort charges** | Plain review queue (accept categories) |
| **3-minute check** | On Home — open-rarely ritual (safe · charges · fees · promo · bills) |
| **After import / bank** | `books_brief` — uncategorized · pending · stale Plaid → Sort charges / re-link |

**Full books** (shell toggle) restores the full nav for month-end / CPA / power users.

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

## APIs

- `GET /api/home/simple` — primary Home payload  
- `POST /api/onboarding/first-run` — atomic first-run (cash + optional card + bill)  
- Existing account/scheduled/profile endpoints for wizards  

## Wealth tips

Only after safety. Educational only: employer match → IRA habit → 529 (if kids) → optional IUL education.  
**Not investment advice.**

## Do this next priority (Simple)

1. Protect checking / red day / rescue  
2. Fees · promo balloon · high-APR debt  
3. Tax set-aside **only** if business entity or tax rate set (self-employed)  
4. Wealth basics (when safe)  
5. Otherwise: **You're clear — open rarely**  

Empty tax vault for a plain personal / W-2 setup is **optional**, never the day-one nag.
