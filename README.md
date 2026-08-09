# LedgerRing

**Open-source freeware** liquidity cockpit — ridiculously powerful engine, **stupid-simple** daily UI.

> **What can I safely spend or charge right now without bouncing checking, without dumb fees, and without unnecessary interest?**

| Pillar | Rule |
|--------|------|
| Checking | **Never negative** |
| 0% cards | Intentional float when payoff path is interest-free |
| Cheap debt vs yield | Opportunity cost (don’t prepay 2.6% mortgage if cash pays 6%) |
| Priorities | Fiscal soundness #1 · credit health #2 |
| Effort | Open rarely — automation first |
| License | Free for anyone · open source |

**v0.7 Simple mode (default):** Home · Add · Can I buy? · Sort charges · 3-minute check  
**Full books:** flip the shell toggle for ledgers, tax, multi-user, reconcile.

Product constitution: [`docs/PRODUCT.md`](docs/PRODUCT.md) · Simple mode: [`docs/SIMPLE_MODE.md`](docs/SIMPLE_MODE.md) · Release: [`docs/RELEASE_0.7.0.md`](docs/RELEASE_0.7.0.md)

## Windows (primary)

```powershell
# Dev
.\scripts\start-winui.ps1

# Grade-A bar + one-folder package
.\scripts\verify-grade-a.ps1
.\scripts\package-release.ps1
# → dist\LedgerRing-Windows-x64\LedgerRing.WinUI.exe  (+ engine\)
```

Install: [`docs/INSTALL.md`](docs/INSTALL.md)

## Mac / Linux / phone — Glance

```bash
./scripts/start-glance.sh
# http://127.0.0.1:7420/glance
ledgerring glance --open
ledgerring home --brief    # Safe to spend + Do this next
```

## Engine quick start

```powershell
python -m venv .venv
# activate, then:
pip install -e ".[dev]"
python -m financial_os.cli serve
# or: ledgerring serve
```

1. WinUI **Get started** (~2 min) or API first-run  
2. Home → **Safe to spend** + **Do this next**  
3. Optional: Add → Link bank / Import CSV  
4. Open rarely — run the **3-minute check** when you return

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
