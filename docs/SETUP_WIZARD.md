# Smart Setup Wizard

Resumable multi-phase onboarding (v1.0.39+). Goal: choose **where books live** + **optional app lock**, then **~2 minutes to Safe to spend**, then **optional power depth** — not a forced tunnel.

## Early steps (all paths)

| Phase | Purpose |
|-------|---------|
| **Welcome** | Time honesty |
| **Storage** | This PC only · OneDrive / Dropbox / iCloud / custom folder (`FOS_DATA_DIR`). Cloud = single-writer honesty. |
| **Security** | App lock: none · PIN · password · Windows Hello (Face ID / fingerprint catalog for future clients). Secrets stay on device. |

## Paths (money)

| Path | Fast flow |
|------|-----------|
| **Plaid (BYOK)** | Keys (local) → Link banks (≤10 Items) → **power menu** → Done |
| **CSV / OFX** | + Cash account loop → **power menu** → Done |
| **Quick manual** | Checking + optional card/bill → **power menu** → Done |

## Power menu (optional depth)

After you have cash, **Make it smarter** offers jump-in modules. Each returns to the menu when finished:

| Module | What it does |
|--------|----------------|
| **Find cards & bills** | Skim cash history for liabilities (no double-schedule on cards) |
| **Recurring** | Remaining bill / subscription patterns |
| **Categories** | High-conf auto + confirm queue (rules + multi-LLM) |
| **Budgets** | Seed from history, edit amounts |
| **Safety buffers** | Total floor + per-account reserves |
| **AI helpers** | Grok / OpenAI / Anthropic / custom keys (local only) |

**I'm ready — go to Home** finishes (requires ≥1 cash account). **Skip** leaves setup open so you can resume later.

## Phases (detail)

1. **Welcome** — time honesty, resume support  
2. **Path** — Plaid / CSV / manual  
3a. **Plaid keys** — [dashboard.plaid.com/signup](https://dashboard.plaid.com/signup); stored in `~/.financial-os/secrets.json` (Windows DPAPI + file lock)  
3b. **Plaid Link** — app opens `/static/plaid-link.html`  
4. **Cash loop** (CSV) or **manual** form  
5. **Power menu** — optional depth (above)  
6. **Done** → Simple Home  

**Skip this step** advances one phase only (or leaves to Home from power menu). Finishing requires ≥1 cash account unless `force_empty`.

## APIs (summary)

| Area | Routes |
|------|--------|
| State | `GET /api/setup/state`, `POST /api/setup/advance`, `POST /api/setup/complete` |
| Cash | `GET /api/setup/cash`, `POST /api/setup/cash-account` |
| Plaid | `POST /api/plaid/credentials`, Link status `n/10` Items |
| AI | `POST /api/ai/credentials` |
| Discover | `GET /api/setup/discover`, `POST /api/setup/discover/apply` |
| Recurring | `GET /api/setup/recurring`, `POST /api/setup/recurring/apply` |
| Categorize | `GET /api/setup/categorize`, `POST .../auto`, `POST .../confirm` |
| Budgets | `GET /api/setup/budgets`, `POST /api/setup/budgets/apply` |
| Buffers | `GET/POST /api/setup/buffers` |

## Buffers model

- **Total** = `app_settings.safety_buffer`  
- **Per-account** = `accounts.safety_buffer`  
- **IFPP effective** = `max(total, sum(per-account capped at balance))`  
- Budget remaining still reserves separately when enabled  

## Secrets

- Path: `%USERPROFILE%\.financial-os\secrets.json`  
- Windows DPAPI for values; exclusive file lock on read-modify-write  
- Not included in SQLite backup zips  
- Settings → **BYOK connections** to edit without re-running the wizard  
- Categorizer tries configured LLM keys in order: Grok (xAI) → OpenAI → Anthropic → custom  

## Fresh dogfood

1. Close app; delete `~/.financial-os/financial_os.db` (optional: secrets.json)  
2. Run WinUI unpackaged  
3. Get started → walk path of choice  

## Related

- [BUDGETS.md](./BUDGETS.md) · [PRIVACY.md](./PRIVACY.md) · [PRODUCT.md](./PRODUCT.md) money-in  
