# Install HonestSpend

Freeware · local-first · Python fiscal engine · **WinUI** (Windows primary) · **Glance** (Mac/Linux/phone browser).

## Recommended layout

```
C:\HonestSpend\
  HonestSpend.WinUI.exe      ← native UI
  … (WinAppSDK runtime files)
  engine\                   ← Python package + .venv
    pyproject.toml
    src\financial_os\
    .venv\
  README-INSTALL.txt
```

The app looks for `engine\` next to the EXE automatically (`BackendHost`).

Data defaults to:

```
%USERPROFILE%\.financial-os\
  financial_os.db
  backups\
  engine.log
  tray.pid
```

Override with **Settings → Data dir** or env `FOS_DATA_DIR` (OneDrive-friendly).

---

## Option A — From source (developers)

```powershell
git clone <repo> financial-os
cd financial-os
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Native UI
.\scripts\start-winui.ps1
# or: cd clients\HonestSpend.WinUI ; dotnet run -c Debug -p:Platform=x64
```

---

## Option B — One-folder Windows package (GitHub / sideload)

**Requires (builder only):** .NET 10 SDK, Python 3.11+  
**End user:** just unzip + double-click — no Python required.

```powershell
# From repo root — grade-A bar first, then package
.\scripts\verify-grade-a.ps1
.\scripts\package-release.ps1
# → dist\HonestSpend-Windows-x64\
# → dist\HonestSpend-Windows-x64.zip
```

What it does:

1. `publish-winui.ps1` → self-contained WinUI  
2. `prepare-engine-bundle.ps1` → `engine\` with venv + package  
3. Copies install notes + Simple mode docs  
4. Zips for distribution  

**End user path**

1. Unzip (keep `engine\` next to `HonestSpend.WinUI.exe`)  
2. Run `HonestSpend.WinUI.exe`  
3. Complete **Get started** (~2 min)  
4. Home shows **Safe to spend** + **3-minute check**  
5. Optional: **Add → Link bank** or Import CSV  

Engine is auto-detected from `.\engine\`. If offline: Settings → **Start engine**.

---

## Option C — MSIX (Microsoft Store track)

**Store product type: MSIX** (not EXE/MSI). Scaffold + Partner Center steps:

```powershell
.\scripts\package-msix.ps1 -CreateSelfSignedCert   # once, local sideload only
.\scripts\package-msix.ps1
# → dist\msix\*.msix
```

See **[docs/MSIX.md](./MSIX.md)** for identity, `runFullTrust` certification notes, and engine packaging strategy.

GitHub zip/Inno (Option B) remains the power-user / freeware sideload path.

---

## Option C — Inno Setup installer (optional)

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php)  
2. Build a package folder first: `.\scripts\package-release.ps1`  
3. Open `packaging\HonestSpend.iss` and compile  
4. Output: `dist\HonestSpend-Setup-x64.exe`  

Signing: set `SignTool` / certificate in the `.iss` if you ship publicly.

---

## macOS / Linux — Glance shell

There is no native Mac/Linux app yet. Use the **same engine + Glance UI**:

```bash
cd path/to/financial-os
chmod +x scripts/start.sh scripts/start-glance.sh
./scripts/start-glance.sh
# → starts engine if needed, opens http://127.0.0.1:7420/glance
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m financial_os.cli serve
# browser: http://127.0.0.1:7420/glance
# CLI:     honestspend glance          # JSON
#          honestspend glance --open   # browser
```

Data: `~/.financial-os/` (override with `FOS_DATA_DIR`).

**Phone on LAN:** bind with care (`FOS_HOST=0.0.0.0` requires API key unless `FOS_ALLOW_NON_LOOPBACK=1` for lab only). Prefer multi-user tokens + encrypted backups for real sharing.

---

## Multi-user & encrypted backups

- Second user → **X-API-Key required** (Users page / `honestspend token`)  
- Encrypted snapshots: WinUI Data page or `POST /api/backup/create-encrypted`  
- See `docs/CLIENTS.md`

---

## First run checklist

1. Launch **HonestSpend.WinUI.exe** (engine auto-starts from `.\engine\`).  
2. **Get started** (~2 min) → checking + optional card/bill.  
3. **Home** → Safe to spend · Do this next · 3-minute check.  
4. Optional: **Add → Link bank** or Import CSV.  
5. **Settings** (optional): logon tray, rainy-day floor.  
6. Automation (optional):

```powershell
# From a full repo clone (or copy scripts next to engine)
.\scripts\register-tasks.ps1
.\scripts\smoke-logon.ps1
```

See [AUTOMATION.md](./AUTOMATION.md).

---

## Upgrading

1. Quit WinUI + tray.  
2. Backup: `python -m financial_os.cli backup --force` (or Data page).  
3. Replace app/engine files; keep `~\.financial-os` data.  
4. Launch; migrations apply on engine start (`schema_meta`).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Backend not found | Place `engine\` beside EXE or set Backend root in Settings |
| Port 7420 busy | Close other `serve` / WinUI instances (single-instance UI) |
| Empty books after path change | Use **Copy DB to path** then restart engine |
| Restore didn’t apply | Restore is staged; restart engine (Data page does this) |
| Docker / remote | Set `FOS_REQUIRE_API_KEY=1` or tokens; avoid open `0.0.0.0` |

---

## Security (local default)

- Engine binds **127.0.0.1** when started by WinUI.  
- No API key required on loopback; required off-loopback unless `FOS_ALLOW_NON_LOOPBACK=1`.  
- Backups are plain SQLite/zip on disk — use OS/OneDrive encryption as needed.

---

## What’s not in the zip

- Plaid credentials (set `FOS_PLAID_*` in environment if used)  
- Grok/xAI key (`FOS_XAI_API_KEY`)  
- Your live database (stays in user profile / `FOS_DATA_DIR`)
