# 1.0 release candidate checklist

**Do not tag `v1.0.0` until every box is checked.**  
Until then: stay on **0.9.x**. Maturity is **~65% dream / 0.6.5-class** — see [VERSIONING.md](./VERSIONING.md).

## A. Dogfood (real household path)

- [ ] Cold start: first-run wizard ≤2 min → Safe to spend on Home  
- [ ] Import CSV **or** Plaid sandbox → **After import / bank** card makes sense  
- [ ] Sort charges: accept ≥1 category  
- [ ] Fee check: confirm or dismiss at least one candidate (or empty queue OK)  
- [ ] Possible bills: add or skip one recurring suggestion  
- [ ] Promo set-aside **or** N/A (no 0% card)  
- [ ] Close the month: walk all required steps once  
- [ ] Tax year prep: walk required steps (or all green)  
- [ ] Backup: local or encrypted snapshot succeeds  
- [ ] Can I buy?: check + optional Save as scenario  

**Result:** ☐ Pass · ☐ Fail notes: _______________

## B. Package (clean machine / clean folder)

- [ ] `.\scripts\verify-grade-a.ps1` green  
- [ ] `.\scripts\package-release.ps1` produces `dist\LedgerRing-Windows-x64\`  
- [ ] Unzip/copy to a path **without** repo clone  
- [ ] `LedgerRing.WinUI.exe` starts engine from `.\engine\`  
- [ ] First-run still works offline-local (no Python on PATH required if bundle built)  
- [ ] Optional: Inno compile → install → launch  

**Result:** ☐ Pass · ☐ Fail notes: _______________

## C. Product gaps (minimum for the 1.0 sentence)

- [ ] Scenario **list / run / delete** in Full books (API already exists)  
- [ ] `docs/RELEASE_1.0.0.md` written (is / isn’t)  
- [ ] About page shows **pre-1.0** until tag flips  
- [ ] No open **P0** from dogfood (data loss, wrong Safe to spend, first-run broken)  

## D. Honesty bar

- [ ] README / About: not “replaces Intuit/Credit Karma”  
- [ ] Explicit: educational credit · tax prep not e-file · multi-entity liquidity books  
- [ ] VERSIONING.md still accurate  

## E. Tag

When A–D are green:

```powershell
# bump __version__ / pyproject / Inno to 1.0.0
.\scripts\verify-grade-a.ps1
git tag -a v1.0.0 -m "LedgerRing v1.0.0 — liquidity OS"
```

**Signed off by:** _______________ **Date:** _______________
