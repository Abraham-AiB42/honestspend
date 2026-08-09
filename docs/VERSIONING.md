# Versioning policy (honest pre-1.0)

LedgerRing uses **two rulers**. Do not confuse them.

## 1. Feature tags (git / `__version__`)

Linear milestone tags for what shipped:

| Tag band | Theme |
|----------|--------|
| **0.6.x** | Public multi-entity · never-neg · Glance · multi-user |
| **0.7.x** | Simple mode · first-run · wizards |
| **0.8.x** | Live books loop · month-close · fees/bills one-tap |
| **0.9.x** | Tax year prep · scenarios · reports · pre-1.0 freeze |
| **1.0.0** | Liquidity OS ship — [RC_1.0.md](./RC_1.0.md) + dogfood e2e green |
| **1.x** | H3 polish (mobile PWA, automation OS, nerd grid) |

Tags **are not** “percent of the dream done.” They mark **slices of capability**.

## 2. Product maturity (dream / “I live here”)

Against [`DREAM_ROADMAP.md`](./DREAM_ROADMAP.md) jobs (Excel · CK · QB · TT · Rocket *jobs*, not clones):

| Estimate | Meaning |
|----------|---------|
| **~65–70%** | At 0.9.x freeze (0.6.5-class on 0→1.0 dream scale) |
| **1.0 promise** | Liquidity OS jobs shippable; not full H3 / not Intuit parity |

**Rule:** Feature tags (0.7–0.9) ran ahead of dream %. **1.0.0** is the honest “you can live on the liquidity promise” line after RC — not “dream finished.”

## 3. After 1.0

1. **1.0.x** — bugfixes and small polish.  
2. **1.1+** — H3 (Glance PWA, automation UI, nerd tools).  
3. Do **not** rewrite old git tags (0.6–0.9 stay as history).

## 4. What 1.0 *will* mean

> Local multi-entity **liquidity OS**: Safe to spend you can trust, Simple open-rarely path, Full books for ledgers/tax/credit math, CSV/Plaid path, month-close and tax-year *prep*.  
> **Not** a bureau, **not** e-file, **not** payroll, **not** a loan marketplace.

If a feature is required for that sentence to be true in real use, it is a **1.0 blocker**.  
If it is H3 (mobile PWA, rule grid, Windows service), it is **1.x**.

## 5. Display

- Engine / About: `Engine v0.9.x` + maturity line (“pre-1.0 · ~65% dream”)  
- `__version__` / pyproject / Inno stay aligned with the **feature tag**
