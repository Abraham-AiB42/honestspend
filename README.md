# LedgerRing

**Open-source freeware** liquidity cockpit for **small business owners** (and anyone) — not a spreadsheet companion, not an investment app.

> **What can I spend or charge right now without going negative on checking, without fees, and without unnecessary interest — using intentional 0% float when it’s smart?**

| Pillar | Rule |
|--------|------|
| Checking | **Never negative** |
| 0% cards | Intentional float when payoff path is interest-free |
| Cheap debt vs yield | Opportunity cost (e.g. don’t prepay 2.6% mortgage if cash pays 6%) |
| Priorities | Fiscal soundness #1 · credit health #2 |
| Effort | Minimum user time — automation first |
| License | Free for anyone · open source |

Product constitution: [`docs/PRODUCT.md`](docs/PRODUCT.md)

Everything lives in the app: accounts, cards, recurrences, live bank links/CSV, IRS-mapped categories, tax packets, multi-entity books.

Legacy spreadsheet import is **optional one-time migration only**.

## Windows client (WinUI 3 — native)

```powershell
# From repo root — engine + optional web
.\scripts\start.ps1

# Native WinUI app (auto-starts engine if needed)
.\scripts\start-winui.ps1
```

**Install / distribute:** see [`docs/INSTALL.md`](docs/INSTALL.md)

```powershell
# Self-contained UI + engine folder + zip
.\scripts\package-release.ps1
# → dist\LedgerRing-Windows-x64\  and  .zip
```

Open Visual Studio → `clients\LedgerRing.WinUI\LedgerRing.WinUI.csproj` for designer/debug.

## Quick start (engine / web)

**Windows**
```powershell
cd path\to\financial-os
.\scripts\start.ps1
```

**macOS / Linux**
```bash
cd path/to/financial-os
chmod +x scripts/start.sh
./scripts/start.sh
```

**Manual**
```powershell
python -m venv .venv
# activate venv, then:
pip install -e ".[dev]"
python -m financial_os.cli serve
# or: ledgerring serve
```

Open **http://127.0.0.1:7420**

1. Complete the **setup wizard** (cash account + optional card)
2. Add bills under **Bills**
3. Feed transactions via **Connect** (CSV or Plaid)
4. Optional tray: `python -m financial_os.cli tray`
5. Optional Docker: `docker compose up --build`

Data: `~/.financial-os/financial_os.db` (or `FOS_DATA_DIR`)

Multi-client: [`docs/CLIENTS.md`](docs/CLIENTS.md)

## Product surfaces

| Tab | Purpose |
|-----|---------|
| Spendable | IFPP number, card float, payoff plans |
| Accounts | Full cash/card editor (due dates, 0% promos) |
| Bills | Recurring expenses/income on an account/card |
| Review | Smart categorize (rules + optional Grok) |
| Ledger | Transactions + tax-line categories |
| Connect | Plaid + bank CSV (primary data in) |
| Tax | 1120-S / Sch A packet for TurboTax |
| Settings | Buffer, horizon, util warns |

## Optional integrations

```powershell
# Grok categorizer
$env:FOS_XAI_API_KEY = "xai-..."

# Plaid bank link
$env:FOS_PLAID_CLIENT_ID = "..."
$env:FOS_PLAID_SECRET = "..."
$env:FOS_PLAID_ENV = "sandbox"

# Fake demo accounts (dev only)
$env:FOS_SEED_DEMO = "1"
```

## CLI

```powershell
python -m financial_os.cli serve
python -m financial_os.cli tray
python -m financial_os.cli digest          # JSON; exit 2 if critical (cron)
python -m financial_os.cli token owner     # mint X-API-Key for multi-client
python -m financial_os.cli tax-packet --profile personal --year 2026
python -m financial_os.cli version
# Legacy migration only:
python -m financial_os.cli import-xlsx path\to\old.xlsx --since 2025-01-01
```

## Open source

- License: [MIT](LICENSE)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Product rules: [docs/PRODUCT.md](docs/PRODUCT.md)

## Tests

```powershell
pytest -q
```

## Disclaimer

Not tax, legal, or investment advice. Tax line maps organize records for TurboTax/CPA; verify filings professionally.
