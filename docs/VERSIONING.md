# Versioning policy (honest pre-1.0)

LedgerRing uses **two rulers**. Do not confuse them.

## 1. Feature tags (git / `__version__`)

Linear milestone tags for what shipped:

| Tag band | Theme |
|----------|--------|
| **0.6.x** | Public multi-entity · never-neg · Glance · multi-user |
| **0.7.x** | Simple mode · first-run · wizards |
| **0.8.x** | Live books loop · month-close · fees/bills one-tap |
| **0.9.x** | Tax year prep · scenarios · reports · **pre-1.0 freeze line** |
| **1.0.0** | Only after [RC_1.0.md](./RC_1.0.md) checklist is green |

Tags **are not** “percent of the dream done.” They mark **slices of capability**.

## 2. Product maturity (dream / “I live here”)

Against [`DREAM_ROADMAP.md`](./DREAM_ROADMAP.md) jobs (Excel · CK · QB · TT · Rocket *jobs*, not clones):

| Estimate | Meaning |
|----------|---------|
| **~65%** | Current honest maturity (as of 0.9.x) |
| **~0.6.5-class** | How it *feels* on a 0→1.0 dream scale |
| **~35% left** | Mostly dogfood, package proof, scenario UX, 1.0 honesty — not random features |

**Rule:** We may be on tag **0.9.x** while product maturity is still **~0.6.5-class**. That is intentional honesty, not a bug.

## 3. Freeze line (until 1.0)

While on **0.9.x**:

1. **Do not tag `v1.0.0`** until every item in [RC_1.0.md](./RC_1.0.md) passes.  
2. Next releases are **`0.9.1`, `0.9.2`, …** (fixes, RC work, small H2 polish).  
3. Marketing / About / README must say **pre-1.0** and **~65% dream**, not “almost 1.0.”  
4. Do **not** rewrite old git tags (0.7–0.9 stay as history).

## 4. What 1.0 *will* mean

> Local multi-entity **liquidity OS**: Safe to spend you can trust, Simple open-rarely path, Full books for ledgers/tax/credit math, CSV/Plaid path, month-close and tax-year *prep*.  
> **Not** a bureau, **not** e-file, **not** payroll, **not** a loan marketplace.

If a feature is required for that sentence to be true in real use, it is a **1.0 blocker**.  
If it is H3 (mobile PWA, rule grid, Windows service), it is **1.x**.

## 5. Display

- Engine / About: `Engine v0.9.x` + maturity line (“pre-1.0 · ~65% dream”)  
- `__version__` / pyproject / Inno stay aligned with the **feature tag**
