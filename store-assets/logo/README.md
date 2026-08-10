# HonestSpend logos (transparent only)

All PNGs have **alpha** (white void removed, content cropped, fitted into squares — not stretched).

| File | Size | Content |
|------|------|---------|
| `icon-300x300.png` | 300×300 | Mark only — **use for Store square logo** |
| `icon-150x150.png` | 150×150 | Mark only |
| `icon-71x71.png` | 71×71 | Mark only |
| `icon-44x44.png` | 44×44 | Small tile |
| `full-logo-300x300.png` | 300×300 | Mark + wordmark |
| `full-logo-150x150.png` | 150×150 | Mark + wordmark |
| `full-logo-71x71.png` | 71×71 | Mark + wordmark |
| `StoreLogo.png` | 300×300 | Same as icon-300 (app package) |
| `*-cropped-transparent.png` | master crops | Reprocess source |

Re-export from Downloads source:

```powershell
python store-assets/logo/process_selected_logo.py
# or: python store-assets/logo/process_selected_logo.py path\to\logo.jpg
```
