# Changelog

## 1.0.30 — Dogfood honesty path · Plaid missing bal signal

- **Dogfood E2E**: CSV import ending bal → set_books CTA → trust → books_brief clear
- **Plaid**: count/report `balances_missing_current` when bank omits snapshot (books not zeroed)
- **books_brief**: watch when linked Plaid cash still has no institution_balance

## 1.0.29 — set_books wins CTA · enter queue · typed trust keeps Sort

- **ShowNextSteps**: `set_books_from_bank` never overwritten by `enter_ending_bal`
- Queue extra bal-less accounts; advance after each Save+set trust
- Typed OFX/PDF/CSV override → trust then ShowNextSteps (keep Sort; skip enter/set_books)
- set_books-only path trusts file bank bal (does not re-apply typed box)
- CSV max-day amount orientation on max-date slice (multi-day files)

## 1.0.28 — OFX/PDF typed bal trusts books · multi enter CTA UX

- **OFX/PDF**: typed ending bal sets bank bal **and** trusts → Safe to spend (parity with Save+set CTA)
- Skip stale `enter_ending_bal` after typed trust; Home CTA instead
- **ShowNextSteps**: select account for set_books / enter_ending_bal; multi-account lists remaining bals
- Set books path uses shared parse (fail loud on bad typed bal)

## 1.0.27 — WinUI ending-bal hygiene · chrono same-day CSV · multi enter_bal

- **WinUI**: `TryParseBankAmount` on CSV import; fail loud if box non-empty but bad
- Clear/rebind ending-bal box on file pick + preview (no stale override)
- OFX/PDF: apply typed ending bal when file did not set bank bal
- **CSV**: same-day ending bal from amount↔running-bal orientation (chrono + newest-first)
- **Inbox**: multi-account `enter_ending_bal` CTAs; clearer empty-file error

## 1.0.26 — Inbox enter_ending_bal · same-day CSV · PDF interest lines

- **Inbox**: bal-less success passes `account_id` → aggregate `enter_ending_bal` CTA
- **CSV**: same-day newest-first ending bal (first row on max date)
- **PDF**: keep dated INTEREST CHARGED / fee activity (headers only without date)
- **WinUI**: ending-bal accepts parentheses / trailing minus
- **Inbox `ok`**: only true on real money-in (created / skip / institution bal)

## 1.0.25 — Signed PDF bal · date-aware CSV ending · inbox bal-only

- **PDF** ending bal keeps sign (overdraft/overpayment); parentheses amounts
- **CSV** ending bal = balance on **max txn date** (newest-first exports correct)
- **Inbox** archives + aggregates set_books when `institution_balance_set` (bal-only OFX/PDF)
- **Refunds**: fee-shaped returns (`RETURN FEE`) stay charges
- **enter_ending_bal** on skip-only bal-less reimport
- Home Do this next awaits set_books trust

## 1.0.24 — PDF New Balance → bank bal · fee id polish

- **PDF**: parse **New Balance / Ending / Closing** into `institution_balance` (CSV/OFX parity)
- Post-PDF next_steps get real drift / set_books / hold when bal matches
- API + preview expose ending_balance / balance_source
- Home fee items: `transaction_id` via `JsonUi.Int` (string|number)

## 1.0.23 — Credit refunds · payment heuristics · OFX/PDF parity · CTA guards

- **Refunds/statement credits** stay positive (reduce owed); payment false positives tightened (protection / late payment / pymt)
- **PDF**: bare `PAYMENT` rows kept; real import test charge+payment
- **OFX credit** uses same normalizer as CSV/PDF
- **Import**: `enter_ending_bal` requires typed ending bal before trust
- **Home**: Do this next elevates Sort when uncat-only; promo `account_id` via JsonUi.Int
- Credit normalizer also runs after amount_sign invert

## 1.0.22 — Credit payment signs · month-close honesty · Plaid tests

- **Credit CSV/PDF**: payments (PAYMENT/AUTOPAY/THANK YOU) stay positive; charges flip; no more inflated owed on pay-downs
- Shared `import_amounts.normalize_credit_import_amount` for CSV + PDF
- **Month-close**: reconcile/set_books **before** Sort charges; sort detail is uncat-only copy
- **Plaid**: `available_credit` fallback on first link; unit tests for snapshot balances / no txn double-apply
- **Home WinUI**: ritual + do_this `account_id` via `JsonUi.Int` (string|number) for one-tap set_books
- Tests: credit charge+payment balance, import_amounts, plaid mock sync

## 1.0.21 — Plaid parity · secondary Sort · ending bal · credit CSV

- **Plaid**: always `/accounts/get` on sync; set `institution_balance` + books from snapshot (no double txn deltas)
- **Month-close**: drift step is one-tap **set_books_from_bank** with worst-drift `account_id`
- **Home WinUI**: secondary **Sort charges** when books_brief lists it; month-close trusts bank
- **Import**: bal-less CSV/PDF → `enter_ending_bal` next step; Save ending bal then trust
- **Credit CSV**: positive charges flip to spend (PDF parity)
- Tests: enter_ending_bal, credit positive charges, month-close set_books action

## 1.0.20 — Honesty first · inbox confidence · trust hygiene

- Home: **books≠bank ranks above Sort charges** (secondary: sort still listed)
- Inbox: min score + unique-margin refuse weak/ambiguous filename matches
- `trust institution`: refresh credit **available_credit** + audit log before/after
- Month-close drift gate is **cash IFPP only** (not card/loan noise)
- Tests: Home set_books, honesty>uncat, weak inbox, multi-cash gate, OFX last-4, credit trust

## 1.0.19 — Inbox honesty closeout · Home books≠bank

- Inbox: aggregate **set_books_from_bank** next_steps (worst drift account)
- WinUI: unmatched files **refused by default** (opt-in “use Target account”)
- OFX last-4 match only when **unique**
- Home **books_brief** + Do this next + 3-min ritual when cash books ≠ bank ending bal
- One-tap trust from Home (Do this next / After import card)

## 1.0.18 — Open-rarely Home · reliable Release publish

- Home: hide **month-close** when period closed; hide **tax year** when complete; compact **3-min ritual** when all clear
- WinUI **PublishTrimmed=false** (Release packaging no longer hits NETSDK1102)
- package-release / CI publish pass `-p:PublishTrimmed=false`; install notes updated for 1.0.x

## 1.0.17 — Month-close honesty gate · CSV txn-id dedupe · quieter Home

- Month-close reconcile: after first bank bal, all cash IFPP accounts need ending bal; drift blocks close
- Month-close “Match bank / Import bank bal” routes to **Import** (Simple), not Full Reconcile
- CSV detects **Transaction ID / FITID / Reference** for stable dedupe across re-exports
- Home: when month is closed, hide step list (open-rarely quiet)

## 1.0.16 — Import honesty + inbox safety + path jails

- **CSV/OFX/PDF import updates `current_balance`** (same rules as manual posts) so Safe to spend moves
- Post-import **Set Safe to spend from bank** when books ≠ bank ending bal (Simple, no Full Reconcile)
- **Inbox** refuses unmatched files (no first-checking fallback / ACCTID poison)
- Upload size limit (50 MB) · safe filenames · path APIs jailed under `data_dir`
- Shared `account_balance` apply/reverse helpers (create + void + import)

## 1.0.15 — Mark month closed + post-import bill hints

- **Schema v12:** `month_close_period` + `month_close_last_at` on app settings
- `POST /api/home/month-close/complete` · Home **Mark month closed** when steps clear
- Month-close card shows closed state for the calendar period
- Post-import next_steps may suggest **possible bills** when recurring patterns exist

## 1.0.14 — CSV ending balance → Reconcile

- Detect **Balance / Running balance** columns on bank CSV
- Last balance (or manual override) sets `institution_balance` for Reconcile + drift
- Import page: optional **Bank ending balance** field · preview fills from file
- Parity with OFX LEDGERBAL post-import path

## 1.0.13 — Inbox next steps + OFX ACCTID auto-match

- **Inbox process** returns aggregated `next_steps` (Sort charges / Home)
- **OFX ACCTID** stored on `Account.external_id` after first import
- Later inbox OFX/QFX files match by ACCTID even with unrelated filenames
- Import page shows match mode + CTAs after “Import inbox now”

## 1.0.12 — Post-import next steps for all money-in paths

- Shared **`build_post_import_next_steps`** (Sort charges · Reconcile · Home)
- **CSV** and **PDF** import return the same structured next_steps as OFX
- Import page CTAs after every file import (not only OFX)

## 1.0.11 — Rename: Floatpile → HonestSpend

- **Product name:** **HonestSpend**
- Full rename: docs, CLI (`honestspend`), WinUI `HonestSpend.WinUI`, packaging, tray/tasks
- Python import path stays `financial_os`; data dir stays `~/.financial-os` (FOS_*)
- Legacy CLI aliases: `floatpile`, `ledgerring`, `financial-os`
- See [`docs/BRAND.md`](docs/BRAND.md)

## 1.0.10 — OFX ledger balance + post-import next steps

- **LEDGERBAL / AVAILBAL** from OFX/QFX → `institution_balance` for Reconcile
- Import result includes **drift** (books vs bank) + structured **next_steps**
- Import page: Sort charges · Reconcile · Home CTAs after OFX import
- CLIENT_FIRST docs list OFX/PDF/inbox formats

## 1.0.9 — OFX/QFX bank import

- **OFX / QFX** preview + import (no extra deps) · FITID dedupe
- Inbox accepts `.ofx` / `.qfx` alongside CSV/PDF
- Import page: Pick OFX/QFX · bank guides mention Quicken formats

## 1.0.8 — Tray inbox + PDF statement import

- **Tray:** Import page · Open inbox folder · Import inbox now (API or offline)
- **PDF statements:** best-effort text extract (`pypdf`) · preview + import · inbox accepts `.pdf`
- First-run tip points at Import guides + inbox
- Prefer CSV when banks offer it; PDF is heuristic

## 1.0.7 — Bank download guides + inbox import

- **Bank guides** on Import: Chase, Amex, Capital One, etc. — login link + steps (no passwords)
- **Inbox drop folder** (`data_dir/inbox`) · `floatpile import-inbox` · `POST /api/import/inbox/process`
- Scheduled task **Floatpile-ImportInbox** (daily 09:00) via register-tasks
- Filename → account nickname matching; processed CSVs archived

## 1.0.6 — Rename: LedgerRing → Floatpile (working alpha)

- **Product name:** **Floatpile** (working alpha at the time)
- Full rename: docs, CLI (`floatpile`), WinUI project `Floatpile.WinUI`, packaging, tray/tasks
- Python import path stays `financial_os`; data dir stays `~/.financial-os`
- Legacy CLI alias `ledgerring` still points at the same entry point
- See [`docs/BRAND.md`](docs/BRAND.md)

## 1.0.5 — Freeware money-in + customizable import reminders

- **Policy:** full freeware · optional **BYOK Plaid** · optional **BYOK Grok** · no paid services  
- **First-run:** choose import reminder cadence (off / daily / weekly / monthly) + focus (transactions / statements / both)  
- **Settings:** money-in section · snooze 7d · mark refreshed  
- **Home / digest:** nudge when due → Import (after fiscal safety)  
- Schema **v11** · `import_reminder_*` + `import_last_at`  

## 1.0.4 — Sort polish · debt CSV · tray deep-links

- **Sort charges** Skip · Accept all shown (learns rules)
- **Reports** Export debt CSV (alongside cashflow)
- **Tray → WinUI deep-link** menu: Sort charges / Reports / Settings via `--page` + `winui.navigate`
- Second-instance launch honors page and activates existing window

## 1.0.3 — Ledger bulk + rule test + task status

- **Ledger bulk categorize** — multi-select checkboxes · apply category to selected (learns rule from first)
- **Rules test-as-you-type** — `POST /api/rules/test` previews matches against recent payees
- **Settings task status** — shows Task Scheduler state for Auto-backup + Daily digest (last/next run)

## 1.0.2 — Client power tools

- **Audit log** Full books page (`GET /api/permissions/audit`)
- **Rules** plain-language match labels + clearer add form
- **WinUI path pointer** (`winui.path`) so tray finds the desktop EXE after launch
- **Reports → Export CSV** cashflow by entity
- Client-first tray path discovery hardened  

## 1.0.1 — Client-first (no PWA)

- **[`docs/CLIENT_FIRST.md`](docs/CLIENT_FIRST.md)** — WinUI is the product; Glance is fallback only  
- Dream H3: **native multi-platform clients**, never PWA  
- **Tray:** Open HonestSpend (**desktop**) first; Glance demoted to browser fallback  
- **Settings:** Register / remove Windows scheduled tasks (backup + digest) from the client  
- About: client-first copy  

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
- CLI: `honestspend home` (+ `--brief`) for Safe to spend summary
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
- CLI: `honestspend glance` (JSON) · `honestspend glance --open`
- `scripts/start-glance.sh` (Mac/Linux) · `scripts/start-glance.ps1` (Windows)
- INSTALL + CLIENTS: Mac/Linux install path via Glance

## 0.5.0 — Packaging (Wave G)

- **Post-setup backup** on quick setup / complete onboarding
- `docs/INSTALL.md` — install options (source, zip, Inno)
- `scripts/package-release.ps1` — WinUI + engine stage + zip
- `packaging/HonestSpend.iss` — optional Inno Setup installer
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

- **Native WinUI 3 app** (`clients/HonestSpend.WinUI`) — not Electron/Tauri
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

- HonestSpend branding, open-source freeware positioning
- Default safety buffer $1,000; never-neg checking
- OSS LICENSE (MIT) + CONTRIBUTING

## 0.1.0 — initial cockpit

- IFPP engine, multi-entity profiles, IRS COA
- Plaid + bank CSV, Excel migrate (legacy)
- Tax packet, categorize rules, Windows tray
