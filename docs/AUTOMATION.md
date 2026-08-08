# LedgerRing — Windows automation

Local-first jobs that keep books safe **without** opening the full UI every day.

## 1. Logon: engine + tray (tray-only)

In **WinUI → Settings**:

1. Check **Start system tray with app** (optional if tray-only).
2. Check **Launch at Windows logon (tray-only)**.
3. Save.

This writes `HKCU\…\Run\LedgerRing` with:

```text
"…\LedgerRing.WinUI.exe" --tray-only
```

On logon:

- Single-instance mutex prevents duplicate UI.
- Engine starts on `127.0.0.1:7420` if down.
- Tray polls Spendable + digest; critical alerts toast once.
- Main window stays hidden; open WinUI again (or **Show window** in Settings) to restore UI.

**Smoke:** `.\scripts\smoke-logon.ps1`

## 2. Scheduled backup (Task Scheduler)

```powershell
.\scripts\register-tasks.ps1
# optional: -BackupHour 3 -DigestHour 8
# remove:  .\scripts\register-tasks.ps1 -Uninstall
```

| Task | Script | Default |
|------|--------|---------|
| `LedgerRing-AutoBackup` | `scripts/task-auto-backup.ps1` | Daily 03:00 |
| `LedgerRing-Digest` | `scripts/task-digest.ps1` | Daily 08:00 |

### CLI used by tasks

```powershell
python -m financial_os.cli backup --auto          # skip if not due
python -m financial_os.cli backup --force --keep 14
python -m financial_os.cli digest                 # exit 2 if critical
python -m financial_os.cli health                 # exit 1 if API down
```

Force backup from env in task runner: `$env:FOS_BACKUP_FORCE=1`.

Backups land in `%USERPROFILE%\.financial-os\backups\` (or `FOS_DATA_DIR\backups`).

## 3. Digest for monitoring

- Exit **0** — no critical alerts  
- Exit **2** — critical (red day, etc.) — suitable for monitoring  
- `task-digest.ps1` may start `serve` if health fails, then runs digest  

Last digest may be written to `~\.financial-os\last_digest.json`.

## 4. Recommended “minimum time” stack

| Layer | What |
|-------|------|
| Logon | `--tray-only` |
| 03:00 | Auto-backup |
| 08:00 | Digest (optional engine wake) |
| Rare UI | WinUI Daily 2-min Home |

## 5. Security notes

- Tasks run as **your user** (Interactive), not SYSTEM.
- Engine stays on **127.0.0.1** when started by WinUI/scripts.
- Non-loopback bind still requires API key / `FOS_ALLOW_NON_LOOPBACK` (see PRODUCT / 0.4.0).

## 6. Uninstall automation

```powershell
.\scripts\register-tasks.ps1 -Uninstall
# WinUI Settings → uncheck Launch at logon
```
