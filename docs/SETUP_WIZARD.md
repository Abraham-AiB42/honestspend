# Smart Setup Wizard

Resumable multi-phase onboarding (v1.0.39+). Goal: cash books → liabilities → bills → categories → budgets → buffers → Home.

## Paths

| Path | Flow |
|------|------|
| **Plaid (BYOK)** | Keys (local) → Link banks (≤10 Items on free trial) → optional AI keys → discover → … |
| **CSV / OFX** | + Cash account loop → bank guide → import → discover → … |
| **Quick manual** | Classic 2‑min cash + optional card/bill |

## Phases

1. **Welcome** — time honesty, resume support  
2. **Path** — Plaid / CSV / manual  
3a. **Plaid keys** — [dashboard.plaid.com/signup](https://dashboard.plaid.com/signup); stored in `~/.financial-os/secrets.json` (Windows DPAPI)  
3b. **Plaid Link** — app opens `/static/plaid-link.html`  
3c. **AI keys** — Grok / OpenAI / Anthropic / custom (optional)  
4. **Cash loop** — type → nickname/bank → guide → import  
5. **Discover** — cards, loans, bills, recurring investments; card payment plans  
6. **Recurring** — remaining bill patterns  
7. **Categorize** — high-conf auto + top payee chips (rules learned)  
8. **Budgets** — seed from history, edit amounts  
9. **Buffers** — total floor + per-account reserves  
10. **Done** → Simple Home  

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
- Not included in SQLite backup zips  
- Settings → **BYOK connections** to edit without re-running the wizard  

## Fresh dogfood

1. Close app; delete `~/.financial-os/financial_os.db` (optional: secrets.json)  
2. Run WinUI unpackaged  
3. Get started → walk path of choice  

## Related

- [BUDGETS.md](./BUDGETS.md) · [PRIVACY.md](./PRIVACY.md) · [PRODUCT.md](./PRODUCT.md) money-in  
