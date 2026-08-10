# Microsoft Store listing assets

Everything you need for Partner Center **Store listings**.

## Paste the copy

Open **`LISTING_COPY.txt`** — full title, short description, long description, features, keywords, privacy URL.

Or use the markdown source: [`docs/STORE_LISTING.md`](../docs/STORE_LISTING.md)

| Field | Value |
|--------|--------|
| Privacy | https://honestspend.net/privacy/ |
| Support | https://github.com/Abraham-AiB42/honestspend/issues |
| Website | https://honestspend.net/ |
| Price | $49.99 list · 50% promo through 2026-10-21 |

## Screenshots

Folder: **`screenshots/`** (1366×768 PNGs, demo data only)

| File | Shows |
|------|--------|
| `01-home.png` | Safe to spend + Do this next |
| `02-import.png` | CSV import + next step |
| `03-books-honesty.png` | Books ≠ bank card |
| `04-sort-charges.png` | Review / fee queue |
| `05-month-close.png` | Month-close checklist |

Upload all five. Caption them as demo / sample data if asked.

Regenerate:

```powershell
.\store-assets\capture-screenshots.ps1
```

## Logo

Folder: **`logo/`** — transparent PNGs only (see `logo/README.md`).

| File | Use |
|------|-----|
| `icon-300x300.png` | Partner Center square logo (mark only) |
| `icon-150x150.png` / `icon-71x71.png` | Smaller tiles |
| `full-logo-*.png` | Mark + wordmark when a larger lockup is needed |
| `StoreLogo.png` | App package (same as icon-300) |

## Package

MSIX with correct identity:  
`dist\msix\HonestSpend.WinUI_1.0.32.0_x64_Test\HonestSpend.WinUI_1.0.32.0_x64.msix`
