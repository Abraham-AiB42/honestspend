# Changelog

## 1.0.0 — Liquidity OS

Local multi-entity **liquidity OS**: Safe to spend · Simple open-rarely · Full books · CSV/Plaid path · month-close · tax-year prep · scenarios · reports.

- **What-if scenarios** Full books page (list / run / delete / quick save)
- **`scripts/_dogfood_e2e.py`** — RC path A automated household journey  
- **`docs/RELEASE_1.0.0.md`** — is / isn’t  
- Grade-A bar includes dogfood + 1.0 docs  

See [VERSIONING.md](docs/VERSIONING.md). Not a bureau, not e-file, not payroll.

## 0.9.1 — Version honesty (pre-1.0 freeze)

- **[`docs/VERSIONING.md`](docs/VERSIONING.md)** — feature tags vs ~65% dream / **0.6.5-class** maturity  
- **[`docs/RC_1.0.md`](docs/RC_1.0.md)** — hard checklist; **no `v1.0.0` until green**  
- Stay on **0.9.x** for fixes/RC work; About + README state pre-1.0 honestly  
- Do not rewrite historical tags (0.7–0.9 remain)

## 0.9.0 — Tax year prep + scenarios + debt report

- **Tax year checklist** on Home + `GET /api/tax/year-checklist` (categorize · vault · packet)
- **Named scenarios** schema **v10** · `GET/POST /api/scenarios` · quick save from Can I buy?
- **Debt snapshot** `GET /api/reports/debt` on Reports page
- Dream H2 slice toward “I live here”

## 0.8.1 — One-tap bills/fees + cashflow report

- **Add this bill** from recurring suggestions (`POST /api/recurring/accept`)
- **Fee check** Yes fee / Not a fee / Skip on Home
- **`GET /api/reports/cashflow`** + Full books **Reports** page (by entity)

## 0.8.0 — Dream H1 live books + month-close

- **`docs/DREAM_ROADMAP.md`** — Excel+CK+QB+TT+Rocket Money *jobs* plan
- **`books_brief`**, **`fee_brief`**, **`recurring_suggestions`**, **`promo_brief`**, **`month_close`**
- APIs: `GET /api/recurring/suggestions`, `GET /api/home/month-close`
- WinUI: After import · Fee check · Possible bills · 0% promo one-tap · **Close the month** · **Move money playbooks**
- 3-minute check: bank re-auth/stale + fee enrichment

## 0.7.1 — Open-rarely polish

- Hide **Get started** after first-run (Simple + Full nav)
- Digest: plain “Sort charges” / “set aside” promo copy
- CLI: `ledgerring home` (+ `--brief`) for Safe to spend summary
- README oriented to Simple mode + grade-A package path

## 0.7.0 — Simple mode + wizards (north star)

Polish: named autopay cards, first-run cold start, empty-state bill tip, Glance `/api/home/simple` + wealth card, plain Can I buy / Credit / Accounts labels.
Tax vault no longer headlines personal day-one (optional unless business / rate set); Home soft-routes to wealth or “open rarely”.
A-path polish: Simple **Sort charges** review, 3-minute check on Home, kill residual `#id` chrome, first-run bank-later step, Settings engine-first.
Ship bar: `scripts/verify-grade-a.ps1`, package-release one-folder install, Inno **0.7.0**, Add → Link bank, Home start-engine + bank tip.

- **`GET /api/home/simple`** — safe to spend, status, do-this-next, setup flags, wealth tips
- **`POST /api/onboarding/first-run`** — atomic first-run (cash + optional card + bill)
- **Wealth basics** (educational, after safety): 401(k) match, IRA habit, 529 if kids, IUL disclaimer
- **WinUI Simple mode** (default nav): Home · Add · Get started · Can I buy? · Review · About
- **First-run wizard** page (cold start) — ~2 min, plain language
- **Add hub + Money wizards**: cash, savings, card, loan, bill, income, business, child
- Home rebuilt around **Safe to spend** + **Do this next** (no IFPP jargon)
- Named pickers for void + payment matches + autopay cards (no raw IDs on happy path)
- Settings: everyday safety first; jargon under **Advanced** expander
- Glance: plain money view, dedicated wealth tips card
- `UiCopy` plain-language helpers (autopay policy, pay method, money view)
- `docs/SIMPLE_MODE.md`

## 0.6.0 — Public multi-entity release

**Ship bar for freeware release:** Personal + Add Business(es) + Add Child(ren); never-neg write gate + rescue coach; multi-user keys; encrypted cloud snapshots; Glance for Mac/Linux/phone; schema **v9**.

### Wave 0 — Generic entities
- **Personal only** on fresh install — no private business names in seed
- **Add Business / Add Child** via `POST /api/profiles` + WinUI **Entities** page
- Child COA (`child_budget`); business COA applied when created
- Schema **v5**: `parent_profile_id`, profile archive, `never_negative_enforcement`
- Stripped private entity names from seed, demo, docs, web, WinUI

### Wave 1 — Never-neg + rescue + scope
- **Write gate**: `never_negative_enforcement` = off|**warn**|hard; `POST /api/transactions` returns **409** `would_go_negative` unless `confirm_unsafe` (warn) 
- **`is_red_now`** on IFPP when checking already negative; digest `red_now` alert
- **Rescue coach**: `POST /api/liquidity/rescue` — transfer-in, defer bills, 0% float, APR cost, sell-investment advisory, intermix
- Scope threaded: digest, fees, promo, debts, capital desk, pre-purchase cards
- **Fee confirm**: `POST /api/fees/confirm` + `GET /api/fees/summary`; schema **v6** `fee_status`
- Quick-setup card defaults due day 15 / close day 1
- WinUI Home **Rescue options** button

### Wave 2 — Books quality
- **Void transaction**: `POST /api/transactions/{id}/void` reverses balances, status=`void`
- **Transfer matcher**: `GET /api/transfers/candidates`, `POST /api/transfers/confirm`
- **Import presets**: `GET/PUT /api/import/presets` (institution sign/map memory)
- **What-if IFPP**: `POST /api/ifpp/simulate` with extra outflows
- **Child allowance** intermix kind + WinUI playbook
- Schema **v7** `import_presets` table
- `scripts/smoke-e2e.ps1` · Ledger void · Buy What-if

### Wave U/S — Multi-user + encrypted cloud backup
- **Multi-user mode**: 2+ active users → **X-API-Key required** even on loopback
- Creating a second user mints **owner_api_token** if owner had none
- Role caps expanded; audit log `GET /api/permissions/audit` (schema **v8**)
- **Encrypted backups**: AES-256-GCM `.lrenc` via `POST /api/backup/create-encrypted`
- Remote destination folder: `GET/PUT /api/backup/remote-config` (OneDrive/Dropbox path)
- Decrypt: `POST /api/backup/decrypt` → staged restore path
- Dependency: `cryptography`
- WinUI Users multi-user banner · Data page encrypted backup UI

### Wave product polish
- **Shell entity bar** on MainWindow (entity + silo/combined) + `AppState.ScopeChanged`
- **CPA mode** toggles read-only nav (hide write pages; tax export stays)
- **Autopay desk**: `GET/PUT /api/autopay` — min \| statement \| promo_sink (schema **v9**)
- **Money map**: `GET /api/intermix/graph` + Intermix page list
- **Fiscal brief**: `GET /api/digest/brief` (offline default; optional Grok)
- Home: Fiscal brief + Fee year summary buttons
- `docs/CLIENTS.md` multi-platform + encrypted sync contract

### Wave detector + glance
- **Card payment matcher**: `GET /api/payments/candidates`, `POST /api/payments/confirm`
- **Pending-aware IFPP**: `pending_count`, `pending_outflows_abs`, `pending_warning` on `/api/ifpp`
- Digest pending alerts include $ outflow (warn if ≥ $100)
- **`GET /api/glance`** — mobile/multi-client one-shot Spendable + alerts
- WinUI: pending banner on Home; payment matches on Reconcile

### Wave P — Glance multi-platform shell
- **`/glance`** + `web/glance.html` — mobile-first Spendable / rescue / brief UI
- CLI: `ledgerring glance` (JSON) · `ledgerring glance --open`
- `scripts/start-glance.sh` (Mac/Linux) · `scripts/start-glance.ps1` (Windows)
- INSTALL + CLIENTS: Mac/Linux install path via Glance

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
