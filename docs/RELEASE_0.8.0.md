# HonestSpend v0.8.0 — Dream H1 (live books + month-close)

First vertical slice of [`DREAM_ROADMAP.md`](./DREAM_ROADMAP.md) H1.

## Ship

```powershell
.\scripts\verify-grade-a.ps1
.\scripts\package-release.ps1
```

## What's new on Simple Home

| Card | Job |
|------|-----|
| After import / bank | Uncategorized · pending · Plaid health |
| Fee check | Fee-like charges |
| Possible bills | Recurring detect from history |
| 0% promo | One-tap sinking set-aside |
| Close the month | Fees · charges · promo · tax · reconcile · backup |
| 3-minute check | Daily open-rarely (bank step enriched) |

## Add hub

- **Move money** playbooks: reimburse · pay myself · fund business · kid allowance  

## APIs

- `GET /api/home/simple` — adds `books_brief`, `fee_brief`, `recurring_suggestions`, `promo_brief`, `month_close`
- `GET /api/recurring/suggestions`
- `GET /api/home/month-close`
