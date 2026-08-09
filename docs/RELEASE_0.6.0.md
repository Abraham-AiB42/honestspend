# Floatpile v0.6.0 — release notes

Open-source freeware liquidity cockpit. Local-first Python engine + WinUI (Windows) + Glance (Mac/Linux/phone).

## Highlights

1. **Public multi-entity** — Personal only on install; **Add Business** / **Add Child**  
2. **Never-neg trust** — write gate (warn/hard) + **rescue coach** with costed options  
3. **Books quality** — void, transfer match, payment match, import presets, simulate  
4. **Multi-user** — second user forces `X-API-Key`; roles + audit  
5. **Encrypted backups** — AES-256-GCM `.lrenc` to any folder (OneDrive/etc.)  
6. **Glance** — `/glance` UI + `floatpile glance` for non-Windows clients  
7. **Schema v9** — migrations applied on engine start  

## Verify

```powershell
pip install -e ".[dev]"
pytest -q
powershell -File scripts\smoke-e2e.ps1
powershell -File scripts\check-no-private-names.ps1
```

## Install

- Windows: [`INSTALL.md`](INSTALL.md) — source, zip, Inno  
- Mac/Linux: `./scripts/start-glance.sh`  

## Upgrade from 0.5.x

1. Backup data dir (`~/.financial-os` or `FOS_DATA_DIR`)  
2. Install 0.6.0 engine  
3. Start once — migrations to schema v9 apply automatically  
4. Existing private-named profiles (if any) are **kept**; new installs only seed Personal  

## Security notes

- Loopback single-user: optional API key  
- Multi-user or non-loopback: key required  
- Prefer encrypted backups for cloud copies — not live multi-writer SQLite  
