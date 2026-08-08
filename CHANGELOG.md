# Changelog

## 0.5.0 — Packaging (Wave G)

- **Post-setup backup** on quick setup / complete onboarding
- `docs/INSTALL.md` — install options (source, zip, Inno)
- `scripts/package-release.ps1` — WinUI + engine stage + zip
- `packaging/LedgerRing.iss` — optional Inno Setup installer
- Release workflow: attach INSTALL/AUTOMATION notes; README in WinUI artifact

## 0.4.5 — Automation (Wave F)

- CLI: `backup` (`--auto` / `--force` / `--keep`), `health`
- Task Scheduler: `register-tasks.ps1`, `task-auto-backup.ps1`, `task-digest.ps1`
- Logon smoke: `smoke-logon.ps1` · docs: `docs/AUTOMATION.md`

## 0.4.4 — Tax wave (Wave E)

- Profile tax geo: `home_state`, `multi_state`, `filing_notes`, `state_allocation_json` (schema v4)
- `PATCH /api/profiles/{id}` · tax packet includes geo + **readiness** gate
- `GET /api/tax/readiness` · CPA pack ZIP (tax CSVs + rules + notes + optional cpa_viewer token)
- WinUI Tax: edit geo fields, readiness, CPA pack download

## 0.4.3 — Ops & quality (Wave D)

- **HTTP tests**: health, IFPP scope, PATCH settings, backup stage, auth 401, reconcile, archive
- **Single-instance WinUI** + second launch shows existing window
- **Tray PID** file (`tray.pid`) prevents stacked trays
- **Engine log** append to `engine.log` under data dir
- **Data dir copy** wizard in Settings (DB + backups folder)
- **Account archive / unarchive** API + Accounts UI Archive button
- List accounts hides archived by default

## 0.4.2 — Live books (Wave C)

- **Reconcile**: `GET /api/reconcile`, set institution balance, trust books|bank · WinUI Reconcile page
- **Plaid lifecycle**: disconnect item, sync age hours, re-auth flag
- **CSV preview**: `POST /api/import/bank-csv/preview` · Import UI mapping sample
- **Settings**: `ifpp_cleared_only`, default `ifpp_scope`; digest pending-txn alert
- Schema **v3**: `institution_balance`, `last_reconciled_at`

## 0.4.1 — Daily product (Wave B)

- **Home Daily (2 min)** mode default: Spendable + capital desk headline + top 3 digest alerts
- Full cockpit: desk detail, digest, promo clock, **fee scan**, promo **sink bill** create
- **Fee scanner** `GET /api/fees/candidates` + digest `fee_detected`
- **Promo sink** `POST /api/promo-clock/{id}/sink-bill` → monthly scheduled expense
- **Can I buy?** structured options (no raw JSON dump)
- **Intermix playbooks**: reimburse / distribution / capital inject

## 0.4.0 — Trust bar (entity Spendable, safe restore, auth)

### Engine
- **Entity-scoped IFPP** (`scope=entity` default, `scope=group` for combined)
- Query: `GET /api/ifpp?profile_id=&scope=` (also capital-desk, digest, pre-purchase)
- **Staged backup restore**: writes `financial_os.db.next` + `.restore_pending`; applied on engine restart
- **Schema migrations** via `schema_meta` + versioned runner (`financial_os.migrations`)
- **Non-loopback security**: require `X-API-Key` unless `FOS_ALLOW_NON_LOOPBACK=1`
- **`PATCH /api/settings`** for true partial updates; `ifpp_scope` setting
- FastAPI **lifespan** (replaces deprecated on_event startup)
- Account `archived_at` column (soft-archive ready)

### WinUI
- Home: **entity switcher** + Entity/Combined scope
- Data: restore stages then **restarts engine** to apply
- `AppState` shared scope; API client passes scope on IFPP/desk/digest/buy

### Ops
- Docker-compose sets `FOS_ALLOW_NON_LOOPBACK=1` for lab (set keys in production)
- Tray: primary action is refresh; web UI demoted to “fallback”
- Web index banner: WinUI is primary client

## 0.3.1 — WinUI 3 Windows client

- **Native WinUI 3 app** (`clients/LedgerRing.WinUI`) — not Electron/Tauri
- Pages: Spendable, capital desk, digest, promo clock, Can I buy?, Settings
- `BackendHost` auto-starts Python engine on `127.0.0.1:7420`
- `scripts/start-winui.ps1`

## 0.3.0 — multi-client foundations

- Tax vault reduces Spendable; income cliffs optional
- Permission stack + optional `X-API-Key` enforcement
- Capital desk, digest, pre-purchase, promo death clock, intermix tools
- Opportunity-cost aware debt plans (yield vs cheap debt)
- Educational credit health + selectable payoff strategies
- Docker / docker-compose + GitHub Actions CI (Win/Mac/Linux)
- Cross-platform start scripts; CLI `digest` / `token` commands

## 0.2.0 — product constitution

- LedgerRing branding, open-source freeware positioning
- Default safety buffer $1,000; never-neg checking
- OSS LICENSE (MIT) + CONTRIBUTING

## 0.1.0 — initial cockpit

- IFPP engine, multi-entity profiles, IRS COA
- Plaid + bank CSV, Excel migrate (legacy)
- Tax packet, categorize rules, Windows tray
