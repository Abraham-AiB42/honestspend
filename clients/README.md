# LedgerRing clients

## Windows (primary) — WinUI 3

Native Fluent Design app. **Not** a WebView / Electron / Tauri wrapper.

```
clients/LedgerRing.WinUI/
  Services/   LedgerApiClient, BackendHost, TrayHost, AppConfig
  Pages/      Spendable … Banks, Data, Users, Tax, Setup, Credit, …
  Helpers/    JsonUi
```

### Run (dev)

```powershell
# From repo root
.\scripts\start-winui.ps1

# Or:
cd clients\LedgerRing.WinUI
dotnet run -c Debug -p:Platform=x64
```

Open `LedgerRing.WinUI.csproj` in Visual Studio for designer/debug.

The app auto-starts `python -m financial_os.cli serve` if nothing is on `:7420` (expects repo-root `.venv`).  
Optional: **Settings → Start system tray with app**.

### Publish / package

```powershell
# Full distributable: UI + engine + zip
.\scripts\package-release.ps1
# → dist\LedgerRing-Windows-x64\  and  .zip

# UI only + optional engine
.\scripts\publish-winui.ps1
.\scripts\prepare-engine-bundle.ps1
```

See [docs/INSTALL.md](../docs/INSTALL.md). Optional Inno: `packaging/LedgerRing.iss`.

Settings: **Launch at Windows logon** (tray-only), **Start tray**, **data dir / OneDrive**, minimized start.  
CLI: `LedgerRing.WinUI.exe --tray-only` · `--minimized` · `--tray`  
Data page: **auto-backup schedule** (engine loop while `serve` is running).

Releases: push a `v*` tag → GitHub Actions builds WinUI zip + Python wheels (`.github/workflows/release.yml`).

### Automation (Windows)

```powershell
.\scripts\register-tasks.ps1          # daily backup + digest
.\scripts\smoke-logon.ps1             # logon/tray/backup checklist
```

See [docs/AUTOMATION.md](../docs/AUTOMATION.md).

MSIX: Visual Studio → **Package and Publish** (update `Package.appxmanifest` Publisher CN + signing cert).

### Native pages (summary)

| Area | Pages |
|------|--------|
| Daily | Spendable, Can I buy?, Bills |
| Money in | Setup, Accounts, Ledger, Review, Rules, Import, Banks (Plaid) |
| Planning | Credit & debts, Tax vault, Tax packet, Intermix |
| System | Data & backup, Users, Settings, About |

### Requirements

- Windows 10 1809+ / Windows 11  
- .NET 10 SDK  
- Windows App SDK (NuGet)  
- Python backend: `pip install -e .` at repo root (+ `pystray` `pillow` for tray)

## Other platforms

Same HTTP API — [docs/CLIENTS.md](../docs/CLIENTS.md).
