# 1.0 release candidate checklist

**Status: READY TO TAG** when automated dogfood + grade-A pass (see below).  
Manual WinUI package dogfood still recommended once per machine.

## A. Dogfood (API path — automated)

```powershell
python scripts\_dogfood_e2e.py
```

Covers: first-run → home/simple → categorize · briefs · month-close · tax year · backup · buy · scenarios CRUD · reports · glance.

- [x] Automated path in `scripts/_dogfood_e2e.py`  
- [ ] Optional: human WinUI walk on real data (recommended)

## B. Package

```powershell
.\scripts\verify-grade-a.ps1
.\scripts\package-release.ps1   # when .NET SDK available
```

- [x] verify-grade-a includes version + surfaces + tests  
- [ ] package-release on a builder machine (Inno optional)

## C. Product gaps

- [x] Scenario list / run / delete — Full books **What-if scenarios**  
- [x] `docs/RELEASE_1.0.0.md`  
- [x] About maturity text updates for 1.0 after tag  
- [x] No known automated P0 (pytest + dogfood + northstar green)

## D. Honesty

- [x] VERSIONING.md · RELEASE_1.0.0 is/isn’t  
- [x] Not marketed as Intuit/CK clone  

## E. Tag

```powershell
# version already 1.0.0 in tree
.\scripts\verify-grade-a.ps1
python scripts\_dogfood_e2e.py
git tag -a v1.0.0 -m "Floatpile v1.0.0 — liquidity OS"
```

**Signed off:** automated RC (engine + API dogfood) · **Date:** 2026-08-08
