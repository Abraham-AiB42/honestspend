# Passoff — HonestSpend

**Date:** 2026-08-11  
**Active work branch:** `main`  
**Version in tree:** **1.0.56** (`honestspend`, `pyproject.toml`, WinUI package)  
**Remote:** https://github.com/Abraham-AiB42/honestspend.git  

Use this file to resume on another PC without re-deriving session context.

---

## 1. What this product is

**HonestSpend** — local-first personal/business finance desktop app (WinUI 3 + Python FastAPI/SQLite).

- **Core promise:** one honest **Safe to spend** number (engine cash − bills − dual buffers − discretionary budget reserve ± pending).
- **Identity:** Agency in Box 42 · MSIX `AgencyinBox42.HonestSpend`
- **Not:** cloud ledger, bank-password vault, multi-writer live SQLite on OneDrive

**Repo layout (high level):**

| Path | Role |
|------|------|
| `src/honestspend/` | Python engine (API, IFPP, setup, crypto, import, budgets) |
| `clients/HonestSpend.WinUI/` | Windows client (wizard, Home, Import, Buy, lock, settings) |
| `docs/` | PRODUCT, SETUP_WIZARD, BUDGETS, SIMPLE_MODE, STATEMENT_CYCLES, … |
| `docs/superpowers/plans/` | Implementation plans |
| `tests/` | pytest (`pythonpath=src`) |
| `site/` | marketing / privacy HTML |
| `scripts/` | package-release, prepare-engine-bundle, dogfood, MSIX |
| `dist/HonestSpend-Windows-x64/` | Full unpackaged ship folder (UI + embeddable engine) — **not** always committed |
| `store-assets/` | Store listing copy + 1366×768 screenshots |

Agent SDD scratch under `.superpowers/` is **gitignored**.

---

## 2. How to run (work PC)

### Prerequisites

- Windows 10/11, Python **3.11+**
- .NET / WinUI SDK as in `HonestSpend.WinUI.csproj`
- Git:

```powershell
git clone https://github.com/Abraham-AiB42/honestspend.git
cd honestspend
git checkout main
git pull
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Engine

```powershell
honestspend serve
# Default: http://127.0.0.1:7420  ·  data: %USERPROFILE%\.HonestSpend
```

### Full consumer package (recommended dogfood)

```powershell
.\scripts\package-release.ps1 -Configuration Release
# Run:
.\dist\HonestSpend-Windows-x64\HonestSpend.WinUI.exe
```

### WinUI only (dev)

```powershell
cd clients\HonestSpend.WinUI
dotnet build -c Release -p:Platform=x64
# Or: .\scripts\publish-winui.ps1
# Point Backend root at repo / engine folder in Settings if needed
```

### Fresh customer wipe (local data only)

```powershell
# Close the app first
Remove-Item -Recurse -Force "$env:USERPROFILE\.HonestSpend" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\HonestSpend" -ErrorAction SilentlyContinue
# Kill orphan engine on 7420 if needed
```

### Tests

```powershell
python -m pytest tests/ -q
# Import / smart review focus:
python -m pytest tests/test_bank_qif.py tests/test_smart_import_review.py tests/test_bank_ofx.py tests/test_import_inbox.py -q
```

---

## 3. What landed since 1.0.55 (resume context)

Tree is **1.0.56**. Recent `main` themes (newest last):

| Theme | Notes |
|-------|--------|
| Multi-account OFX | `import_ofx_multi`, ACCTID match / auto-create |
| Drag-drop any bank file | Import page primary drop zone; multi-file queue |
| Smart multi-file plan | Personal vs business scoring; `POST /api/import/smart/*` |
| Cross-format dedupe | `import_dedupe` fingerprints across CSV/OFX/PDF |
| Post-import bootstrap | APR/promo/limits, recurring, categorize when possible |
| Full rename | package `honestspend`, data `~/.HonestSpend` (no financial-os) |
| Engine lifecycle | Engine stops with app (not always-on service); seal async |
| Setup → Import | First-run **Import bank files** → `NavigatePublic("import")` drop zone |
| **QIF support** | `bank_qif.py`, `/api/import/qif`, inbox + smart plan + WinUI |
| **Idiot-proof review** | Smart plan cards: bank label, last-4, bal, dates, payees, “what we will do” |
| Path button fix | Bottom **Continue with Import bank files** (was dead “Pick a path above”) |
| Store screenshots | Drop-zone first mock; listing mentions QIF |

### Consumer import path (intended)

1. Get started → storage/security as needed → path  
2. **Import bank files** (or **Continue with Import bank files**)  
3. Drop CSV / OFX / QFX / **QIF** / PDF / XLSX (many at once)  
4. Smart review cards name accounts from **file contents** (not user guessing filenames)  
5. **Looks good — import everything** → bootstrap + Home Safe to spend  

### Key code map

| Area | Paths |
|------|--------|
| QIF | `src/honestspend/services/bank_qif.py`, `tests/test_bank_qif.py` |
| Smart plan + review | `src/honestspend/services/smart_import.py`, `tests/test_smart_import_review.py` |
| API import | `src/honestspend/api/app.py` (`/api/import/qif`, smart plan/commit) |
| Inbox | `src/honestspend/services/import_inbox.py` |
| WinUI Import | `clients/.../Pages/ImportPage.xaml(.cs)`, `LedgerApiClient.ImportQifAsync` |
| First-run path | `clients/.../Pages/FirstRunPage.xaml.cs` (`ChoosePathAsync` → import) |
| Package | `scripts/package-release.ps1`, `scripts/prepare-engine-bundle.ps1` |

---

## 4. Dogfood checklist (work PC)

1. Fresh wipe → launch package → first-run wizard  
2. Path: bottom button **and** Import card both open drop zone  
3. Drop a messy multi-file dump (CSV + OFX + **QIF** if you have it)  
4. Confirm review cards show bank / last-4 / balances without user “which file is which”  
5. Import everything → books + optional Set Safe from bank  
6. Close app → confirm nothing left listening on **7420**  
7. Optional: Store PNG upload from `store-assets/screenshots/`  

---

## 5. Open / next (not done)

- **Store 10.1.2.10:** resubmit MSIX after python312.dll-in-package fix (see `docs/STORE_CERT_ANALYSIS.md`)  
- Store package cert + Partner Center listing upload  
- Multi-day real-household dogfood  
- Bump CHANGELOG **1.0.56** section from Unreleased when cutting release notes  
- Optional: richer bank-export “how to download” guides with screenshots per institution  
- Optional: QIF investment / split detail (currently simple bank/card txns)  

---

## 6. Version history (recent)

| Ver | Theme |
|-----|--------|
| **1.0.56** | Rename, smart multi-import, QIF, drop-zone first-run, review cards, package |
| **1.0.55** | Trust bar + statement cycles wave |
| **1.0.54** | Envelope raid UI, float whisper, DEK protect-only |
| **1.0.53** | One Safe number (tray/glance), cash-first buy |

---

## 7. Do not

- Force-push `main` without review  
- Commit `.superpowers/` SDD ledgers or local `~/.HonestSpend` data  
- Ship Store without `engine\python\python314.dll` in the MSIX (zip-only extract fails 10.1.2.10)  
- Ship Store without `engine-portable.zip` / cert notes  
- Reintroduce `financial-os` / FloatPile product strings or default data dirs  

---

## 8. Resume one-liner

```powershell
git clone https://github.com/Abraham-AiB42/honestspend.git; cd honestspend; git pull
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e ".[dev]"
.\scripts\package-release.ps1
# then dogfood dist\HonestSpend-Windows-x64\HonestSpend.WinUI.exe
```
