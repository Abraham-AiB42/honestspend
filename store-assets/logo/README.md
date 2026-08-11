# HonestSpend logos

## Package / Start tiles (MSIX — Store 10.1.1.11)

Generate the full on-device tile set into `clients/HonestSpend.WinUI/Assets/`:

```powershell
# From repo root (needs Pillow)
python scripts/generate-store-tiles.py
```

That writes scale-100 + scale-200 squares, wide tile with **HonestSpend** wordmark,
StoreLogo 50×50, splash, and multi-size `AppIcon.ico` on brand navy `#0F2744`.

## Partner Center listing logos

| File | Size | Use |
|------|------|-----|
| `icon-300x300.png` | 300×300 | Store square logo (1:1) |
| `icon-150x150.png` | 150×150 | Optional |
| `icon-71x71.png` | 71×71 | Optional |
| `icon-44x44.png` | 44×44 | Small |
| `StoreLogo.png` | listing copy | Same family as package |

Re-crop source mark only:

```powershell
python store-assets/logo/process_selected_logo.py path\to\logo.jpg
python scripts/generate-store-tiles.py
```
