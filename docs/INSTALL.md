# Install LedgerRing

Freeware · local-first · Python fiscal engine · **WinUI** (Windows primary) · **Glance** (Mac/Linux/phone browser).

## Recommended layout

```
C:\LedgerRing\
  LedgerRing.WinUI.exe      ← native UI
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
# or: cd clients\LedgerRing.WinUI ; dotnet run -c Debug -p:Platform=x64
```

---

## Option B — Publish zip (self-contained UI + engine)

**Requires:** .NET 10 SDK, Python 3.11+

```powershell
# From repo root
.\scripts\package-release.ps1
# → dist\LedgerRing-Windows-x64\
# → dist\LedgerRing-Windows-x64.zip
```

What it does:

1. `publish-winui.ps1` → self-contained WinUI  
2. `prepare-engine-bundle.ps1` → `engine\` with venv + package  
3. Copies install notes into the folder  
4. Zips for distribution  

Unzip anywhere, run `LedgerRing.WinUI.exe`.

---

## Option C — Inno Setup installer (optional)

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php)  
2. Build a package folder first: `.\scripts\package-release.ps1`  
3. Open `packaging\LedgerRing.iss` and compile  
4. Output: `dist\LedgerRing-Setup-x64.exe`  

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
# CLI:     ledgerring glance          # JSON
#          ledgerring glance --open   # browser
```

Data: `~/.financial-os/` (override with `FOS_DATA_DIR`).

**Phone on LAN:** bind with care (`FOS_HOST=0.0.0.0` requires API key unless `FOS_ALLOW_NON_LOOPBACK=1` for lab only). Prefer multi-user tokens + encrypted backups for real sharing.

---

## Multi-user & encrypted backups

- Second user → **X-API-Key required** (Users page / `ledgerring token`)  
- Encrypted snapshots: WinUI Data page or `POST /api/backup/create-encrypted`  
- See `docs/CLIENTS.md`

---

## First run checklist

1. Launch **LedgerRing.WinUI.exe** (engine auto-starts).  
2. **Setup** → primary checking (+ optional 0% card).  
   - Completing setup creates a **post-setup backup** automatically.  
3. **Settings** (optional): logon tray-only, data dir, safety buffer.  
4. Automation (optional):

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
