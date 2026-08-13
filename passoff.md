# Passoff — HonestSpend

**Date:** 2026-08-12  
**Active work branch:** `main`  
**Version in tree:** **1.0.57** (`pyproject.toml`, WinUI). This session’s work is **Unreleased** on top of 1.0.57.  
**Remote:** https://github.com/Abraham-AiB42/honestspend.git  

Use this file to resume on the work PC tomorrow without re-deriving session context.

Abraham is dogfooding. Call him Abraham.

---

## 1. What this product is

**HonestSpend** — local-first personal + business finance desktop app (WinUI 3 + Python FastAPI/SQLite).

- **Core promise:** one honest **Safe to spend** number.
- **Identity:** Agency in Box 42 · MSIX `AgencyinBox42.HonestSpend`
- **Not:** cloud ledger, bank-password vault, multi-writer live SQLite on OneDrive

| Path | Role |
|------|------|
| `src/honestspend/` | Python engine |
| `clients/HonestSpend.WinUI/` | Windows client |
| `docs/` | PRODUCT, SETUP_WIZARD, PRIVACY, … |
| `tests/` | pytest (`pythonpath=src`) |
| `scripts/start-winui.ps1` | **Dev launch** (repo `.venv` + WinUI) |

---

## 2. How to pick up on the work PC

```powershell
cd <repo>   # or: git clone https://github.com/Abraham-AiB42/honestspend.git
git checkout main
git pull
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
.\scripts\start-winui.ps1
```

**Fresh first-run on the work PC** (do this so you see the same chrome Abraham just wiped at home):

```powershell
# Close the app first
Remove-Item -Recurse -Force "$env:USERPROFILE\.HonestSpend" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\HonestSpend" -ErrorAction SilentlyContinue
# Do not copy live Downloads / Statements into git
```

Home PC was already wiped 2026-08-12: `%USERPROFILE%\.HonestSpend` emptied, extra OneDrive HonestSpend data folder deleted. Next home launch is a clean wizard.

**Tests that cover this session:**

```powershell
python -m pytest tests/test_statement_match.py tests/test_smart_import_review.py tests/test_setup_wizard.py tests/test_activity_xlsx_import.py tests/test_promo_statement_parse.py tests/test_bank_qif.py -q
```

---

## 3. What this session shipped (unreleased on 1.0.57)

### First-run chrome

- **No sidebar** until Get started is finished (no Settings rail, no Who / View header, no pane toggle).
- **Get started is not a sidebar item** after that. Optional depth is offered in the wizard; later from Home.
- Steps **1–3** (Welcome, books folder, app lock) work **while the engine starts**. Chip at the top says so.
- Skip is **gone** on steps 1–3. None-lock is the way to skip encryption.
- After import: **Categorize → Budgets**, not a stuck Import page.
- Looks good — import everything uses a **5 minute** HTTP timeout.
- No books file on disk ⇒ first-run wizard even if a stale `SetupComplete` flag lingered.

### Smart import (the big dogfood gap)

Drop transaction downloads **and/or** statements. Engine should match accounts and pull forecast facts (balances, due/close, APR, limits, promos, ISB, Equal Pay, deferred interest, APY, rewards).

**Matching**

- Last-4 + brand families.
- **Capital One and Discover are one family.** Discover stays the consumer brand (footer may say Capital One).
- Overlapping posted transactions (`date|abs cents`). QIF/OFX/CSV emit `txn_fps`. Need 2 overlaps if the statement has ≥2 fingerprints, else 1.
- Unique last-4 can attach even when bank labels differ.
- Credit-union membership PDF → multiple shares (`00`/`01`/`02`), `apply_balance_only`, attach to OFX tails.
- Vehicle finance PDF → loan (Vehicle Description + Motor Finance), not checking.

**Review UI**

- Named businesses; add/remove business; skip account.
- Store branding (Home Depot not Citi, Target not TD) when the file says so.
- Loan labels (Home loan, Car loan · model, Student loan).
- **Same account as N. Title (QIF · filename)** on every numbered card. Pick from either card. Commit normalizes so the statement attaches to the download.
- **More detail** lists date + payee + **amount**.
- Analyze & map shows a spinner immediately.

**Commit fixes**

- QIF path now records `created_ids` so a statement can apply onto the QIF account.
- `commit_smart_plan` work list unpacks `(index, filename, bytes)` correctly (was broken for multi-file).
- Attach lookup can match by `source_key` if file index drifted.

### Statement / promo parse (sanitized fixtures only)

- Amex `activity.xlsx`.
- Ending balance prefers New/Total Balance.
- Wells EXPIRES, Amex intro, HD deferred, Equal Pay, ISB lump, BT offers.
- D10: never silently overwrite user-sourced promo lines.
- D12: books own txns; statement is cycle facts.

### Privacy (non-negotiable)

Live Downloads / Statements are **local training data only**.

**Never commit or paste into git / PASSOFF / chat logs:**

- Real names, last-4s, balances, addresses, VINs
- Live filenames that encode last-4s or account ids
- `%USERPROFILE%\.HonestSpend` books

Tests use synthetic last-4s (`4411`, `5678`) and generic names (`Document.pdf`, `transaction_download.qif`).

---

## 4. Intended first-run path (work PC)

1. Launch `.\scripts\start-winui.ps1` — **full-window wizard, no sidebar**.
2. Welcome → books folder → lock (None is fine). Engine may still be starting.
3. Import bank files → drop Downloads + Statements together.
4. Analyze & map (spinner). Confirm owners / types.
5. If a statement and a QIF/CSV are the same card but not auto-paired: **What to do → Same account as…** (filename is in the label).
6. Looks good — import everything → **Categorize** → **Budgets** → Home. Sidebar appears. Get started is gone.

---

## 5. Key code map

| Area | Paths |
|------|--------|
| Smart plan / cluster / fingerprints / QIF attach | `src/honestspend/services/smart_import.py` |
| Amex xlsx | `src/honestspend/services/activity_xlsx.py` |
| Statement PDF (vehicle + CU membership) | `src/honestspend/services/statement_pdf.py` |
| Promo layouts | `src/honestspend/services/promo_statement_parse.py` |
| Wizard phases (no skip 1–3) | `src/honestspend/services/setup_wizard.py` |
| Import review + same-account + post-import jump | `clients/.../Pages/ImportPage.xaml(.cs)` |
| Wizard + engine-behind-1–3 | `clients/.../Pages/FirstRunPage.xaml(.cs)` |
| Hide pane until done | `clients/.../MainWindow.xaml(.cs)`, `AppState.ShowSetupNav` |
| First-run detect / wipe flags | `clients/.../Services/StorageLocationService.cs` |
| Tests | `tests/test_statement_match.py`, `test_smart_import_review.py`, `test_setup_wizard.py`, `test_activity_xlsx_import.py` |

---

## 6. Open / next (work PC)

- **Walk the clean first-run** end to end with the real dump (local only). Confirm: no sidebar, 1–3 without waiting, statement↔QIF link, categorize → budgets, then sidebar.
- Capital One statement PDFs often extract almost no dated txn lines — fingerprint auto-match can fail; manual **Same account as** is the path. Improving Cap One PDF text extraction is still open.
- Store 10.1.2.10 / Partner Center still open (see `docs/STORE_CERT_ANALYSIS.md`).
- Do not bump to 1.0.58 until this dogfood pass feels solid.
- Optional: richer per-bank “how to download” guides.

---

## 7. Do not

- Force-push `main`
- Commit `~/.HonestSpend`, OneDrive HonestSpend copies, or live bank files
- Put real last-4s, balances, VINs, or live filenames in tests or this file
- Reintroduce a Get started sidebar item
- Use the stale packaged exe from earlier August builds — run `start-winui.ps1` or a freshly packaged `dist\`

---

## 8. Resume one-liner

```powershell
git checkout main; git pull
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# optional fresh wizard:
# Remove-Item -Recurse -Force "$env:USERPROFILE\.HonestSpend"
.\scripts\start-winui.ps1
```
