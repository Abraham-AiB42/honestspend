# Multi-client architecture

Floatpile is **API-first**. One local (or Docker) engine; any client speaks HTTP JSON.

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  WinUI 3     │  │  Web (lite)  │  │  iOS/Android │  │  CLI / Tray  │
│  primary     │  │  Plaid Link  │  │  glance later│  │              │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │                 │
       └─────────────────┴────────┬────────┴─────────────────┘
                                  │  HTTP + optional X-API-Key
                           ┌──────▼──────┐
                           │  FastAPI    │  127.0.0.1:7420
                           │  SQLite     │  fiscal truth
                           └─────────────┘
```

## Today

| Client | How |
|--------|-----|
| **WinUI 3 (native)** | `clients/Floatpile.WinUI` — primary desktop |
| **Web** | Frozen minimal + **Plaid Link** only |
| **Windows tray** | `python -m financial_os.cli tray` |
| **CLI** | `serve`, `digest`, `token`, `backup`, `health`, … |
| **Docker** | `docker compose up` — lab only; set API keys |

## Auth

| Mode | Behavior |
|------|----------|
| Single-user loopback | No key = local owner |
| Multi-user (2+ active users) | **X-API-Key required** on all API routes |
| Non-loopback bind | Key required unless `FOS_ALLOW_NON_LOOPBACK=1` |
| Roles | `owner` · `bookkeeper` · `cpa_viewer` · `viewer` |

CPA desktop: WinUI **CPA mode** hides write nav; use a `cpa_viewer` token for export-only API access.

## Encrypted multi-device sync

Do **not** share a live SQLite file on OneDrive for concurrent writers.

1. `POST /api/backup/create-encrypted` → `.lrenc` (AES-256-GCM)  
2. Copy to user folder (OneDrive/Dropbox/iCloud path via remote-config)  
3. Other device: decrypt → staged restore → restart engine  

## Future clients (same API — no fiscal fork)

| Platform | MVP path |
|----------|----------|
| **macOS / Linux** | Engine + thin **Glance** browser shell until a **native** client exists — **not** a PWA product ([CLIENT_FIRST.md](./CLIENT_FIRST.md)) |
| **iOS / Android** | Safari/Chrome → same `/glance` (or wrap WebView later); auth via API key |

### Contract clients must implement

- `GET /api/health` — readiness  
- **`GET /api/glance`** — one-shot Spendable + alerts + pending (mobile MVP)  
- `GET /api/ifpp?scope=entity|group&profile_id=` — Spendable detail  
- `GET /api/digest` — full alerts  
- `POST /api/liquidity/rescue` — avoid-negative options  
- `GET /api/digest/brief` — optional prose  
- Header `X-API-Key` when multi-user  

**Never re-implement IFPP math in the client.** Call the engine.

### Glance response (mobile)

```json
{
  "cash_spendable": "4000.00",
  "is_red_now": false,
  "next_red_day": null,
  "pending": { "count": 0, "outflows_abs": "0.00" },
  "alerts": [],
  "headline": { "action": "...", "title": "..." }
}
```

## WinUI notes

- Shell **entity bar** (header): entity + Entity/Combined scope for all pages  
- **CPA mode** button: hide write pages  
- Engine auto-start on `127.0.0.1:7420`  

## Glance UI (Mac / Linux / phone)

| Item | Value |
|------|--------|
| URL | `http://127.0.0.1:7420/glance` |
| File | `web/glance.html` |
| Start | `scripts/start-glance.sh` · `scripts/start-glance.ps1` |
| CLI | `floatpile glance` · `floatpile glance --open` |

Features: Spendable, red-now, pending, alerts, rescue options, fiscal brief. Optional API key field for multi-user.

## OpenAPI

With engine running: `http://127.0.0.1:7420/docs`
