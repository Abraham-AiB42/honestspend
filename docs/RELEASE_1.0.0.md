# HonestSpend v1.0.0 — Liquidity OS

**Ridiculously powerful engine · stupid-simple daily UI · local-first freeware.**

## What 1.0 **is**

| Promise | How |
|---------|-----|
| **Safe to spend** you can explain | IFPP · buffer · tax vault · pending · silo/group |
| **Open rarely** | Simple Home · Do this next · 3-minute check · month-close |
| **Never bounce / dumb interest** | Never-neg gate · rescue · 0% float · promo set-aside · fee triage |
| **Multi-entity** | Personal · Business · Child · Move money playbooks |
| **Books path** | CSV/Plaid · Sort charges · recurring detect · reconcile |
| **Tax prep handoff** | Vault · year checklist · packet / CPA pack export |
| **Credit without theater** | Educational model · debt plan · not a bureau score |
| **What-if** | Scenarios list/run/delete · Can I buy? |
| **Trust** | Local SQLite · backup · multi-user keys · encrypted snapshots |

## What 1.0 **is not**

- Credit Karma / bureau pulls / loan marketplace  
- TurboTax e-file or full form engine  
- QuickBooks payroll, invoicing, inventory  
- Investment brokerage / trading  
- Guaranteed multi-device live SQLite cloud  

## Surfaces

| Mode | Nav |
|------|-----|
| **Simple** | Home · Add · Get started (cold) · Can I buy? · Sort charges · About |
| **Full books** | + Entities, ledger, import, banks, credit, tax, reports, **what-if scenarios**, users, … |
| **Glance** | Thin read shell only — **not** a PWA product; clients are native-first ([CLIENT_FIRST.md](./CLIENT_FIRST.md)) |

## Install

```powershell
.\scripts\verify-grade-a.ps1
.\scripts\package-release.ps1
# Run: dist\HonestSpend-Windows-x64\HonestSpend.WinUI.exe
# Keep engine\ next to the EXE
```

See [INSTALL.md](./INSTALL.md) · [RC_1.0.md](./RC_1.0.md) · [VERSIONING.md](./VERSIONING.md).

## Schema

**v10** — includes `whatif_scenarios`.

## Honesty

Feature work through 0.7–0.9 was aggressive on **tags**. Maturity at 0.9.x was ~**65% dream**.  
**1.0** means the **liquidity OS promise** above is shippable and RC-checked — not that every H3 platform idea is done.
