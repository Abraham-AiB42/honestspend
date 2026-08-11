# Passoff — HonestSpend / financial-os

**Date:** 2026-08-11  
**Version on `main`:** **1.0.54** (`3fc857e`)  
**Remote:** https://github.com/Abraham-AiB42/honestspend.git  
**Branch:** `main` (clean, pushed)

Use this file to resume on another PC without re-deriving session context.

---

## 1. What this product is

**HonestSpend** — local-first personal/business finance desktop app (WinUI 3 + Python FastAPI/SQLite).

- **Core promise:** one honest **Safe to spend** number (IFPP engine: cash − bills − dual buffers − discretionary budget reserve ± pending).
- **Identity:** Agency in Box 42 · MSIX `AgencyinBox42.HonestSpend`
- **Not:** cloud ledger, bank-password vault, multi-writer live SQLite on OneDrive

**Repo layout (high level):**

| Path | Role |
|------|------|
| `src/financial_os/` | Python engine (API, IFPP, setup, crypto, budgets) |
| `clients/HonestSpend.WinUI/` | Windows client (wizard, Home, Buy, lock, settings) |
| `docs/` | PRODUCT, SETUP_WIZARD, BUDGETS, PRIVACY, INSTALL, … |
| `tests/` | pytest (pythonpath=`src`) |
| `site/` | marketing / privacy HTML |
| `scripts/` | dogfood helpers |

---

## 2. How to run (new PC)

### Prerequisites

- Windows 10/11, Python **3.11+** (3.14 used in last session)
- .NET **10** / WinUI SDK as in `HonestSpend.WinUI.csproj`
- Git clone:

```powershell
git clone https://github.com/Abraham-AiB42/honestspend.git
cd honestspend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Engine

```powershell
# From repo root with venv active
honestspend serve
# or: python -m financial_os.cli serve
# Default: http://127.0.0.1:7420  ·  data: %USERPROFILE%\.financial-os
```

### WinUI (unpackaged)

```powershell
cd clients\HonestSpend.WinUI
dotnet build -c Debug -p:Platform=x64 -p:WindowsPackageType=None
# Run the produced HonestSpend.WinUI.exe (engine auto-starts via BackendHost)
```

### Tests

```powershell
python -m pytest tests/ -q
# Focused honesty suite often used recently:
python -m pytest tests/test_db_crypto.py tests/test_ifpp_card_float.py tests/test_ifpp_autopay_pending.py tests/test_ifpp_pending_float.py tests/test_budget_reserve_max.py tests/test_budget_group_reserve.py tests/test_pre_purchase_cash_first.py tests/test_pre_purchase_envelope_raid.py tests/test_cli_encrypted_gate.py tests/test_setup_wizard.py -q
```

### Fresh-user dogfood

1. Close app  
2. Optional wipe: delete `%USERPROFILE%\.financial-os\financial_os.db` (+ `.sealed`, `crypto.json` if testing crypto)  
3. Launch WinUI → **Get started**  
4. Path: Storage → Security → money path → power menu → Home  

---

## 3. Version stack (what landed recently)

| Ver | Theme | Tip commit |
|-----|--------|------------|
| **1.0.54** | Envelope raid UI, float whisper, DEK protect-only | `3fc857e` |
| **1.0.53** | One Safe number (tray/glance), cash-first buy, DataDir fail-closed | `592709b` |
| **1.0.52** | Crypto side-channels, migrate/seal, pending→float, fixed budget skip | `0950a2d` |
| **1.0.51** | Autopay skip, cleared_only, Why-this-number, lock chip | `7f19ced` |
| **1.0.50** | Seal-before-kill, no silent boot, unlock fail-closed, best-card float, max-per-cat reserve | `1ce0127` |
| **1.0.49** | Full DB encryption (AES-GCM seal) tied to app lock | `6ed84d4` |
| **1.0.48** | Setup: storage + security first | `02664a8` |
| **1.0.47** | 2-min primary + power_menu depth | `95469b2` |
| **1.0.46** | Setup honesty (skip, cash-required complete, no double card bills) | `284ed29` |

Full notes: `CHANGELOG.md`.

---

## 4. Architecture you must not break

### Setup wizard order

```
welcome → storage → security → path → (manual | cash_loop | plaid_*) → power_menu → done
```

- **Power depth** (jump from menu, return to menu): `discover`, `recurring`, `categorize`, `budgets`, `buffers`, `ai_keys`  
- Code: `src/financial_os/services/setup_wizard.py` · UI: `FirstRunPage.xaml.cs`  
- Manual create: `complete_setup: false` → lands on power_menu  

### Encryption / lock (critical)

| Piece | Location | Rule |
|-------|----------|------|
| Seal file | `financial_os.db.sealed` | AES-256-GCM (`db_crypto.py`) |
| Key wrap | `crypto.json` | PIN/password KEK wraps DEK; Hello = client DEK |
| Process open | `db_runtime.py` | Encryption on → **never silent boot**; unlock restores DEK |
| Seal before kill | `App.SealThenStopEngine`, `BackendHost.RestartAsync` | Seal **then** kill engine |
| CLI/tray offline | `refuse_offline_open_if_encrypted()` | **No** `init_db` beside sealed vault |
| Hello DEK | WinUI `AppLockService` | **DataProtection only** (no cleartext LocalSettings) |
| Health | `/api/health` | `books_ready`, `needs_unlock`, `db_unlocked`, `data_dir` |
| Shell | LockPage + 423 → ForceLock | Fail closed until books open |

**Product honesty:** sealed at rest when sealed; **plaintext while app is open** (cloud folder syncs live files — single-writer only).

### Safe to spend formula (Home / tray / glance / Can I buy cash)

```
IFPP cash_spendable  (bills + dual buffers + tax vault + optional pending)
  − discretionary budget reserve  (max remaining per category; skip housing/util/insurance/debt groups)
  = safe_to_spend
```

- Multi-card float = **best single card**, not sum  
- Card autopay schedules on credit accounts **excluded** from cash runway  
- Tray: `/api/home/simple` · Glance: `safe_to_spend` field  

### Key modules

| Concern | Files |
|---------|--------|
| IFPP engine | `engine/ifpp.py`, `services/ifpp_service.py` |
| Budgets / reserve | `services/budget_service.py` |
| Home payload | `services/home_simple.py` |
| Can I buy | `services/pre_purchase.py` + `BuyPage` |
| Crypto | `services/db_crypto.py`, `db_runtime.py` |
| Storage paths | `services/paths.py`, `StorageLocationService.cs` |
| App lock UI | `AppLockService.cs`, `LockPage`, MainWindow lock chip |
| Secrets BYOK | `services/secrets_store.py` (DPAPI + file lock) |
| Discover | `services/setup_discover.py` (no scheduled card bill) |

---

## 5. What was intentionally left open (next work)

Not blocking, but good candidates after dogfood:

1. **Idle auto-seal** after inactivity  
2. **SQLCipher page-level** (current design is whole-file seal while closed)  
3. **Group Home budget cards** multi-entity summary (note exists; cards still one entity)  
4. **`ifpp_cleared_only` Settings UI** (wired in engine; may not be obvious in Settings)  
5. **Plaid available vs current** for depository balances  
6. **Stronger cloud single-writer** lockfile across devices  
7. **Real dogfood** on second PC with sealed OneDrive path + PIN  
8. **Store submission** packaging / Partner Center (marketing assets under `store-assets/`, `site/`)  
9. **Recovery phrase** if you ever reject “forget PIN = gone forever”  

---

## 6. Known footguns (read before debugging)

| Symptom | Likely cause |
|---------|----------------|
| Empty books after unlock | Old bug: leftover empty plaintext preferred over sealed — **fixed in 1.0.52** (prefer sealed); wipe empty `.db` if local residual |
| 423 everywhere | Encryption on, not unlocked — use Lock page / PIN |
| Tray import fails encrypted | By design offline path refuses seal — open app first |
| “Seal & lock” with no PIN set | Chip hidden when no lock/encryption (1.0.52+); encryption-only seals without LockPage junk PIN |
| Wrong data folder after move | Set DataDir in Settings, use **Move books** (bundle), not raw file copy |
| Safe to spend “too low” | Dual buffer max + discretionary budgets + pending strict; check **Why this number?** |
| Rent double-count | Fixed-obligation budget groups skipped from reserve (1.0.52); schedule still counts bill |

---

## 7. Commands cheat sheet

```powershell
# Sync
git pull origin main

# Version
python -c "from financial_os import __version__; print(__version__)"

# Engine
honestspend serve

# Tests
python -m pytest tests/ -q

# WinUI
cd clients\HonestSpend.WinUI
dotnet build -c Debug -p:Platform=x64 -p:WindowsPackageType=None
```

**Env (optional):**

| Var | Purpose |
|-----|---------|
| `FOS_DATA_DIR` | Books folder (also set by WinUI LocalSettings `DataDir`) |
| `FOS_XAI_API_KEY` | Grok categorizer |
| `FOS_PLAID_*` | Plaid (or in-app secrets.json) |
| `FOS_LICENSE_ENFORCE` | Store builds |

**Local data (not in git):**

```
%USERPROFILE%\.financial-os\
  financial_os.db          # plaintext while open
  financial_os.db.sealed   # at-rest when sealed
  crypto.json              # wrap metadata (not DEK for password? wait - wrapped DEK is here)
  secrets.json             # BYOK (excluded from backups by design)
  backups\
```

**Do not commit:** `winui.path`, local DBs, secrets, `.venv`.

---

## 8. Session history (this machine)

Multi-day autonomous build: setup wizard redesign → power menu → storage/security → full encryption → e2e honesty RCs (1.0.50–1.0.54).

Last shipped: **1.0.54** envelope raid, float whisper, DEK protect-only.

Git status at passoff: **`main` = `origin/main`**, no local dirty tree expected after this commit.

---

## 9. Resume checklist (tomorrow)

- [ ] `git pull origin main`  
- [ ] Confirm `from financial_os import __version__` → `1.0.54`  
- [ ] Run focused pytest suite (section 2)  
- [ ] Optional: dogfood Get started + PIN + seal + unlock  
- [ ] Pick next from section 5 (or Store packaging / real multi-PC cloud dogfood)  

---

## 10. Product constitution (locked decisions)

- Local-first · never store bank passwords  
- CSV/OFX free path · Plaid/LLM **BYOK**  
- Rules-first categorize · optional multi-LLM  
- Skip ≠ complete · finish needs cash  
- No multi-writer live SQLite on cloud sync  
- Simple Home default · Full books for power tools  

Details: `docs/PRODUCT.md`, `docs/SETUP_WIZARD.md`, `docs/BUDGETS.md`, `docs/PRIVACY.md`.

---

*Handoff written for continuity across machines. Prefer updating this file when shipping the next version.*
