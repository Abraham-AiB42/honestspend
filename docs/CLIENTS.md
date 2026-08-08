# Multi-client architecture

LedgerRing is **API-first**. One local (or Docker) server; any client speaks HTTP.

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Web UI     │  │  Desktop    │  │  iOS/Android│  │  CLI/Tray   │
│  (bundled)  │  │  (WebView)  │  │  (future)   │  │             │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │                │
       └────────────────┴───────┬────────┴────────────────┘
                                │  HTTP + optional X-API-Key
                         ┌──────▼──────┐
                         │  FastAPI    │  :7420
                         │  SQLite     │
                         └─────────────┘
```

## Today

| Client | How |
|--------|-----|
| **WinUI 3 (native)** | `clients/LedgerRing.WinUI` — first-class Windows app |
| **Web** | Bundled at `/` when server runs (dev / remote) |
| **Windows tray** | `python -m financial_os.cli tray` |
| **CLI** | `serve`, `digest`, `token`, `tax-packet`, … |
| **Docker** | `docker compose up` — any browser on LAN |

## WinUI 3 (Windows-first)

```powershell
# Terminal 1 (optional — WinUI can auto-start the engine)
cd path\to\financial-os
.\scripts\start.ps1

# Terminal 2
cd clients\LedgerRing.WinUI
dotnet build -c Debug
dotnet run
```

Native pages: **Spendable**, **Can I buy?**, **Settings**, **About**.  
Engine: Python IFPP API on `127.0.0.1:7420` (started by WinUI if not running).

**No Electron / Tauri / WebView shell** for the primary Windows client.

## Auth between clients

1. Create user: Settings → Permission stack (or `cli token`)  
2. Store API token securely on the client  
3. Send header: `X-API-Key: lr_…`  
4. No key = local owner (desktop convenience)

## Future clients (same API)

- **iOS/Android** — SwiftUI/Kotlin → API + token  
- **macOS / Linux** — native later; same HTTP contract  

Do **not** fork fiscal logic into clients — engines stay in Python.
