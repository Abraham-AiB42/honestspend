# HonestSpend v0.7.0 — Simple mode (north star)

**Ridiculously powerful engine · stupid-simple daily UI.**

## Ship bar

```powershell
.\scripts\verify-grade-a.ps1
# pytest · northstar e2e · smoke · name-gate · version · surfaces

.\scripts\package-release.ps1
# → dist\HonestSpend-Windows-x64\  (+ zip)
# Run: dist\HonestSpend-Windows-x64\HonestSpend.WinUI.exe
# Engine auto-detected from .\engine\
```

Optional installer: compile `packaging\HonestSpend.iss` after package-release → `dist\HonestSpend-Setup-x64.exe`.

## What users get

| Mode | Surfaces |
|------|----------|
| **Simple** (default) | Home · Add · Get started · Can I buy? · Sort charges · About |
| **Home** | Safe to spend · Do this next · **3-minute check** · wealth basics · bank tip |
| **Add** | Wizards + **Link bank** + Import CSV |
| **Full books** | Entire cockpit (ledger, tax, users, reconcile, …) |

## North-star guarantees

1. Never bounce checking (write gate + rescue)  
2. Intentional 0% float when due day is set  
3. One Do this next + alternatives + why  
4. Wealth tips only after safety  
5. Personal day-one is **not** a tax-vault nag  
6. Named pickers (no raw IDs on happy path)  
7. Glance multi-platform shell (`/glance`)  

## Upgrade from 0.6

- Schema unchanged (still v9 autopay)  
- New APIs: `GET /api/home/simple`, `POST /api/onboarding/first-run`  
- WinUI defaults to Simple mode  

## Verify locally

```powershell
python -m pytest -q
python scripts\_northstar_e2e.py
.\scripts\verify-grade-a.ps1
```
