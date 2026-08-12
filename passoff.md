# Passoff — HonestSpend / financial-os

**Date:** 2026-08-11  
**Active work branch:** `main` (1.0.55 merged from `feature/statement-cycles`)  
**Version aligned in tree:** **1.0.55** (`financial_os`, `pyproject.toml`, WinUI package)  
**Remote:** https://github.com/Abraham-AiB42/honestspend.git  
**`main` tip:** `0c0baca` — soft-until formula docs; full 1.0.55 line on main  

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
| `src/financial_os/` | Python engine (API, IFPP, setup, crypto, budgets) |
| `clients/HonestSpend.WinUI/` | Windows client (wizard, Home, Buy, lock, settings) |
| `docs/` | PRODUCT, SETUP_WIZARD, BUDGETS, SIMPLE_MODE, STATEMENT_CYCLES, … |
| `docs/superpowers/plans/` | Implementation plans (solid-A, statement cycles, …) |
| `tests/` | pytest (pythonpath=`src`) |
| `site/` | marketing / privacy HTML |
| `scripts/` | dogfood / MSIX / store tiles |

Agent SDD scratch lives under `.superpowers/` and is **gitignored**.

---

## 2. How to run (new PC)

### Prerequisites

- Windows 10/11, Python **3.11+**
- .NET / WinUI SDK as in `HonestSpend.WinUI.csproj`
- Git clone:

```powershell
git clone https://github.com/Abraham-AiB42/honestspend.git
cd honestspend
git checkout main
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### Engine

```powershell
honestspend serve
# Default: http://127.0.0.1:7420  ·  data: %USERPROFILE%\.financial-os
```

### WinUI (unpackaged)

```powershell
cd clients\HonestSpend.WinUI
dotnet build -c Debug -p:Platform=x64 -p:WindowsPackageType=None
# Run HonestSpend.WinUI.exe (engine via BackendHost / embed)
```

### Tests

```powershell
python -m pytest tests/ -q
# Honesty-focused suite:
python -m pytest tests/test_schedule_mark_paid.py tests/test_void_card_payment_schedule.py tests/test_safe_until_cap.py tests/test_autopay_recompute.py tests/test_bank_csv.py tests/test_import_inbox.py tests/test_promo_float_from_lines.py tests/test_wave1_never_neg.py tests/test_home_simple.py tests/test_coming_up.py -q
```

---

## 3. Branch state (`main` @ 1.0.55)

**Shipped on main (CHANGELOG 1.0.55 + solid-A plan):**

- Statement cycles, Coming up, mark-paid honesty, import schedule advance
- Soft until single-count formula, autopay_policy sole authority
- WinUI: progressive Credit, Bills mark-paid, Settings plain labels, Store tiles 1.0.55
- Solid-A plan: `docs/superpowers/plans/2026-08-11-solid-a-grades.md`

**Hygiene (done):** `feature/statement-cycles` fast-forwarded into `main` and pushed (`0c0baca`).

**Still open ops (not code):** Store package cert, multi-day dogfood, optional delete remote feature branch.

---

## 4. Dogfood checklist (resume here)

1. First-run → cash + bill → Home Safe readable  
2. CSV import matching bill → Coming up advances; Import success bar  
3. Show all Coming up uses **same window prefs** as Settings  
4. Mark paid Home + Bills; warn never-neg dialog  
5. Soft until: with window bills can be **below** STS (single-count); hide when equal  
6. Card mark-paid → next date +1 month only; void cash voids transfer pair  
7. Credit: Statement & payment open; promo/learn collapsed  

---

## 5. Version history (recent)

| Ver | Theme |
|-----|--------|
| **1.0.55** | Trust bar + statement cycles wave (on `main`) |
| **1.0.54** | Envelope raid UI, float whisper, DEK protect-only |
| **1.0.53** | One Safe number (tray/glance), cash-first buy |

---

## 6. Do not

- Force-push `main` without review  
- Commit `.superpowers/` SDD ledgers  
- Ship Store without `engine-portable.zip` / cert notes in packaging docs  
