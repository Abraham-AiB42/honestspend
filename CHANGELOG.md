# Changelog

## Unreleased

- **Budget income (step 1)** uses the employer on personal deposits, not the payroll processor. ADP / Gusto / Paychex on a business book is paying staff, not income.
- **Setup budgets is three steps** (US household map, BLS 2024 + doxo/CFPB/YNAB): **1 Income** (paychecks and repeating deposits), **2 Recurring bills** (mortgage/rent, cell, internet, electric, water, insurance, loans, streaming — confirm, edit, or move to a variable budget), **3 Variable envelopes** (groceries, dining, fuel, shopping, …). Subcategories (work lunch, warehouse club, …) are in the map for later tracking. Recalculate still clusters Payment-to / EZ-PAY / City of X, never uses a one-off spike as a monthly envelope, and never under-budgets leftover spend.
- **Categorize**: Continue to budgets sits under the page title so you can leave without scrolling the payee list.
- **Categorize**: Discover `DIRECTPAY FULL BALANCE` is a card payment (not spend) and a pay-statement-in-full signal. From/To on that line is cash → the card, not Discover → —. Dining wording (`restaurant`, pizza, Domino’s, Rock Bottom), Rumble and YouTube Premium as subscriptions, Farmers Ins as insurance.
- **Import last-4**: unique last-4 attaches a statement to that download even if the activity file was guessed as checking (Chase `Type=Sale` exports with no Card column). Filename `…_Activity_YYYYMMDD` is a card. Get started no longer forces Create new when last-4 already hit a book.
- **Can I buy + BNPL**: Promo includes Buy now, pay later (Klarna, Afterpay, Affirm, PayPal Pay in 4, Zip, Sezzle). The check uses first payment + checkout/origination/service fees, shows typical late fees without assuming a miss, and existing BNPL still due. Commit posts installment 1 and schedules the rest.
- **BNPL future charges**: Klarna, Affirm, Afterpay, PayPal Pay in 4, Zip, and Sezzle activity (`2 of 4`, `3/12`) become finite upcoming bills for the remaining installments — Coming up and cash runway see them. Posted BNPL lines stay as shopping spend, not transfers. Zip never matches a ZIP code.
- **Split-this-charge statements**: Plan It / Flex Pay / My Chase Plan / Pay over Time / “4 payments of $X” tables become purchase plans like Amazon ISB when the charge is already on the card.
- **Monthly installments are payment plans**: Apple Card `MONTHLY INSTALLMENTS (11 OF 24)` (and same-shaped activity) becomes a `purchase_plan` like Amazon Interest Saving Balance — remaining principal, monthly, payments left. Not a transfer and not a Sort spend line. Parallel same-amount plans on one day are one combined plan.
- **Card pay-from**: import learns which cash account pays each card (payment-to / last-4 / same-amount pairs). Not shown on Import. Accounts & cards lists it and lets you change it; Full books / Safe to spend already use `payment_funding_account_id`.
- **Engine on first run**: the money engine does not start at launch until you pick a books folder. After books exist, launch starts it as before.
- **Get started**: tapping a suggested books folder continues (no extra Use this location). Import review hides the extra pickers/options under the mapped cards. **Add more files** sits under the mapped list. Merge here is the surviving account (book + type); the folded-in file’s charges land there. Categorize no longer keeps a leftover Personal profile_id.
- **Auto-categorize**: payments (thank-you / autopay / web payment / payment-to-card), mortgage, HOA, interest, fees, and a national merchant catalog. CSV/XLSX keep the issuer Category and Type columns and map them (Groceries, Phone/Cable, Credit Card Payments, …). Learned rules still win. No household names or live files in the catalog.
- **Merchant catalog depth**: NRF Top 100 retailers, Technomic Top 50 restaurant chains, Chase’s 17 spend labels, major utilities / streamers / gyms / EV chargers, grocery banners (Kroger / Albertsons / Ahold families), and statement aliases (`WM SUPERCENTER`, `AMZN MKTP`, Toast `TST*`).
- **First-run wizard chrome**: no sidebar, Settings rail, or Who/View header until Get started is finished. Get started is not a nav item after that.
- **Wizard steps 1–3 while the engine starts**: Welcome, books folder, and No-lock can be answered immediately. Import waits for the engine. PIN/password/Hello still need the engine for encryption.
- **Post-import tunnel**: Looks good — import everything jumps to Categorize, then Budgets (not a stuck Import page). Smart commit timeout is 5 minutes.
- **Smart import matching**: Capital One + Discover are one family; statements attach by last-4, brand family, or overlapping transaction fingerprints (date + cents). QIF/OFX now emit fingerprints; commit records QIF account ids so a statement can land on the QIF card.
- **Same account as…**: every numbered card can link to another file in the batch (QIF/CSV/OFX/PDF). Direction is normalized so the statement attaches to the download.
- **More detail**: sample postings show date + payee + amount.
- **Statement facts**: vehicle finance PDFs → car loan; credit-union membership PDFs → share accounts; Discover stays Discover after the Capital One merger footer.
- **Privacy**: live bank downloads stay local. Never commit real names, last-4s, balances, or live filenames.

- **Store 10.1.2.10 (python3xx.dll)**: MSIX now ships unpacked `engine\python\` + `python3*.dll` next to `python.exe` (package files). Launch refuses PATH/`WindowsApps` python and refuses `python.exe` without a sibling `python3*.dll`. Package-local engine is preferred over LocalAppData extract (packaged child could not load extracted DLLs).
- **Embeddable CPython 3.14.7** (newest stable): `prepare-engine-bundle.ps1` default was 3.12.8. Launch/packaging require versioned `python314.dll` (not the `python3.dll` stub) plus an `honestspend` package.
- **Simple Home**: card setup is due day + pay-from only; budget desk and Rescue/Money brief stay off the first screen (rescue only when status is danger).
- **Full HonestSpend rename**: Python package `honestspend` (was `honestspend`); data dir `~/.HonestSpend` (legacy `~/.HonestSpend` still opens); DB `honestspend.db` (legacy `honestspend.db` still opens); user-facing HonestSpend/HonestSpend strings removed
- **Setup storage freeze fix**: engine `Stop()` no longer deadlocks WinUI via sync seal; progress status + indeterminate bar while moving books; 45s restart timeout

- **Trust bar (full review follow-through)**:
  - Home: STS primary; soft until = **runway − budgets − Coming up outflows once** (not Safe minus bills again); capped at Safe (hidden when same $); “Until …” plain labels; risk line = **Next risk day** only
  - Full **Bills · Mark paid** (same match / never-neg confirm as Home Coming up)
  - Mark paid: match-before-post; `require_match_txn_id` + savepoint auto-advance (no create-on-miss); card side skips already-paired payments; WinUI 409 never-neg confirm dialog
  - Post-import bill advance: CSV/OFX/**Plaid**/**PDF**; Import UI shows advanced count + non-fatal `schedule_advance_error`
  - Promo: installment lines own balloon (`effective_promo_balance` on IFPP, promo clock, autopay sink, debt)
  - Policy: reverse-sync `payment_option` ← `autopay_policy`; Credit All-cards list read-only (edit under Statement & payment)

- **Business entity (AP Agency steals)**: versioned bill series (rent steps), pay-from cash vs card + vendor, **owner draw**, thin **payroll package** (net + employer tax; Bills **Payroll day** UI; legs get `series_id`; cash-only funding), `opex_class` / `income_source`, **entity year P&L** (Reports year picker) — see [BUSINESS_ENTITY.md](docs/BUSINESS_ENTITY.md)
- **Coming up strip**: Simple Home list of cash-side bills / card payments / optional paychecks through **calendar 7–14 days** or **next 1–2 paydays** (`auto` when income known); full-window totals + truncated list; Settings picker; empty-state hint; prefs `coming_up_*`; `GET /api/coming-up` + embed on home/simple; owner draw as outflow; payroll day legs when scheduled
- **Statement cycles**: close day + due day + pay policy → projected **statement balance** and **next payment**; cash **pay-from** schedule so Safe to spend sees the right card outflow (no day grid; multi-entity; promo installment lines; recompute after books mutations)
- **Pay policies + honesty**: **Pay current balance** (full books); **when cash leaves** (due day / day before close / on close) for near-zero statement strategies; utilization pay-to-target; est. interest if min; educational credit advice (not a score)
- **Promo payoff calendar**: dynamic month span from remaining ÷ monthly (e.g. **24 mo** financing), multi-line merge; Credit UI + `GET …/promo-calendar`
- **Cash runway API**: day-by-day cash calendar (`GET /api/cash-runway`) for tools/Full; Home risk uses **Next risk day** only (not a busy-day strip)
- **Statement freeze**: record bank actual vs projected (`…/statement-cycles/freeze`); Credit UI history
- **Rewards pick**: category → best card by rates (`GET /api/rewards/pick`); Credit **Best card**
- **Simple mode**: Home whisper only for soon card payments (within 14 days); full Credit desk stays Full books
- Docs: soft until post-reserve + Simple language table ([SIMPLE_MODE.md](docs/SIMPLE_MODE.md)); [BUSINESS_ENTITY.md](docs/BUSINESS_ENTITY.md), [STATEMENT_CYCLES.md](docs/STATEMENT_CYCLES.md), PRODUCT contract
- **Smart charge audit**: `prefer=auto` is the ranker (interest-free float, then rewards). A safe card beats cash. PRODUCT / Simple / Budgets / Buy no longer say auto prefers cash. Dogfood and northstar assert a card rec on the rewards+float fixture.

## 1.0.55 — Trust bar + Simple mode e2e fixes + Store cert

- **Self-contained engine**: `prepare-engine-bundle.ps1` ships **CPython embeddable** under `engine\python\` (end users do not install Python); prefers embed over system `python`
- **Tray**: same embeddable `ResolvePython` path as the fiscal engine
- **PATCH amount**: reverse/re-apply books balance so Safe stays honest
- **Home**: promo account id kept for one-tap set-aside; SuccessBar for books/promo/month-close; quiet-day density; feedback bars at top
- **Do this next**: Safe ≤ $0 always rescue; fee_brief + promo_brief before Sort
- **Discover/Review/FirstRun**: conservative credit select; Review bulk ≥0.85; recurring default off; buffer warning when bal &lt; floor
- **Card-first recurring**: detect on any account; default schedule on majority account (cards); IFPP skips **all** credit-account schedules from cash Safe (not only autopay names)
- **Store tiles (10.1.1.11)**: regenerate full scale-100/200 set + wide wordmark; brand `#0F2744`; multi-size `AppIcon.ico`; `ShowNameOnTiles`
- **Store crash**: activate window before engine; soft-fail `OnLaunched` / `NavView_Loaded`; MSIX build requires `engine-portable.zip`
- **Promo float**: interest-free capacity subtracts existing promo/card balance (no APR-by-accident)
- **Card float**: no double-count of scheduled net on top of IFPP cash
- **Never-neg**: intermix checks checking outflows (409 + rescue payload)
- **Pending honesty**: pending txns do not apply to books balance; clear applies once
- **Discover/recurring**: skip liability payees; merge-in bills default unselected
- **Do this next**: bank re-auth, fiscal desk before sort; promo one-tap; tax vault navigates
- **Simple UI**: quieter shell (single Who), Success bar, Import more-options, jargon pass
- **Tray**: Safe-first tooltip; offline “Open HonestSpend”
- **Secrets**: DPAPI protect failure refuses plaintext store
- **Review**: accept-all requires ≥70% confidence; batch reports uncategorized total

## 1.0.54 — Polish: envelope raid, float whisper, DEK protect-only

- **Can I buy**: `envelope_raid` offer + `allow_envelope_raid` → `safe_raid_envelope`; WinUI **Raid envelope** button
- **Home**: whisper “+ $X interest-free on best card”; All-money budget scope note
- **Hello DEK**: no cleartext LocalSettings fallback (DataProtection only; migrate legacy once)
- API `POST /api/pre-purchase` accepts `allow_envelope_raid`

## 1.0.53 — P1: one Safe number everywhere

- **Glance** exposes `safe_to_spend` (post budget reserve) matching Home
- **Tray** polls `/api/home/simple` (fallback glance/IFPP); shows Home Safe + locked state
- **Can I buy auto** prefers cash when Safe to spend covers; card capped by Safe to spend
- **DataDir match** fail-closed when `FOS_DATA_DIR` / AppConfig.DataDir is set
- **Discover** higher select thresholds (bills ≥0.75; credit/loan ≥0.65)
- **docs/BUDGETS.md** max-per-category + fixed-obligation skip

## 1.0.52 — P0: crypto side-channels + Safe honesty

- **CLI/tray** refuse offline DB open when encrypted/sealed (no empty plaintext next to vault)
- **Unlock** prefers sealed over empty/stale leftover plaintext
- **Migrate books**: seal → full bundle copy → restart; Settings uses same path
- **Start/Restart engine** seals before kill
- **Pending strict mode** recomputes card float after cash cut
- **Budget reserve** skips fixed-obligation groups (housing/utilities/insurance/debt)
- **Why-this-number** shows effective dual buffer
- **Lock chip** hidden when no lock/encryption; seal-only without junk PIN
- **No lock** requires secret and disables encryption

## 1.0.51 — P2: simpler surface, cleaner IFPP

- **Card autopay schedules** excluded from cash runway (card payoff path owns them)
- **`ifpp_cleared_only` wired**: True = reserve pending outflows in Safe to spend; False = warning only
- **Group budget reserve**: All money sums envelopes across entities
- **Home “Why this number?”** expander + one-liner under Safe to spend
- **Title-bar Lock chip**: Seal & lock / Books locked — one tap
- pre_purchase reserve respects IFPP scope

## 1.0.50 — E2E honesty RC (encryption + IFPP + budgets)

- **Seal before kill**: WinUI seals books before stopping the engine on exit
- **No silent plaintext boot** when encryption is on (crash leftovers still need unlock)
- **Unlock fail-closed**: shell opens only when `books_ready` / `db_unlocked`
- **423 → Lock screen**; health includes `data_dir`, `books_ready`, `needs_unlock`
- **Multi-card float** = best single card (not sum of every card’s capacity)
- **Budget reserve** = max remaining per category (no daily+weekly+monthly stack)
- **Hello DEK** protected with DataProtectionProvider; password enable no longer returns DEK
- **Books-bundle copy** (db, sealed, crypto.json, license) + engine data_dir match on restart
- Refuse re-enable encryption without disable (no silent DEK rotation)

## 1.0.49 — Full DB encryption with unlock key

- **At-rest encryption**: when app lock ≠ none, books use AES-256-GCM seal (`honestspend.db.sealed` + `crypto.json` key wrap)
- **Unlock key hierarchy**: PIN/password → KEK (PBKDF2) → DEK; Windows Hello uses client-held DEK
- Engine boots sealed (HTTP 423 until unlock); `POST /api/crypto/unlock|enable|lock|disable`
- WinUI: lock screen unseals; setup/Settings enable encryption with lock; seal on clean shutdown
- NullPool SQLite so Windows can seal without file locks; poison plaintext if delete fails
- Docs: forget PIN = unrecoverable sealed books

## 1.0.48 — Storage + app lock in setup

- **Setup first steps**: **Where books live** (local / OneDrive / Dropbox / iCloud / custom) then **App lock**
- Cloud folder = user’s sync path; single-writer honesty; engine restarts with `FOS_DATA_DIR`
- **App lock**: none · PIN · password · Windows Hello; launch gate + Settings reconfigure
- Capability catalog for future Face ID / Touch ID / Android biometrics (stubs)
- Lock secrets stay on the client (not SQLite / not backups); engine stores metadata only
- APIs: `GET/POST /api/setup/storage`, `GET/POST /api/setup/security`

## 1.0.47 — 2-min primary + optional power depth

- **Wizard reshape**: primary paths end at **power_menu** (not a forced tunnel through discover→budgets)
- **Power menu**: jump to discover / recurring / categorize / budgets / buffers / AI keys; each returns to menu
- **Manual first-run** can leave `complete_setup=false` so optional depth stays open
- **Multi-LLM categorizer**: xAI → OpenAI → Anthropic → custom (OpenAI-compatible + Anthropic Messages)
- **Secrets file lock** around secrets.json RMW (Windows `msvcrt` / POSIX `fcntl`)
- **Can I buy**: busy state, empty books hint, amount validation
- Docs: SETUP_WIZARD power menu map

## 1.0.46 — Setup honesty + UX fixes (review RC)

- **No IFPP double-count**: credit discover creates account + payment plan only (no scheduled card payment bill)
- **Idempotent discover apply** (skip existing names)
- **Complete requires cash** unless `force_empty=true`
- **Skip this step** = `skip_phase` only (never force-complete)
- **Buffer status** uses same IFPP pool as engine
- **Can I buy** caps every cash option by Safe to spend
- **Removed liabilities phase**; categorize exact payee match; permissions for `/api/setup` + `/api/ai`
- WinUI: engine retry gate, phase titles, Plaid item gate, useGrok when key present, chrome refresh
- Budgets GET no longer seeds by default (`POST /api/setup/budgets/seed`)

## 1.0.45 — Setup wizard polish (PR7)

- **docs/SETUP_WIZARD.md** — full phase map, APIs, buffers, secrets
- **Dogfood** covers setup state/cash/discover/recurring/categorize/budgets/buffers + BYOK status
- **Settings**: BYOK Plaid keys + Link + AI keys; per-account buffer editors + total floor
- PRODUCT money-in points at smart wizard

## 1.0.44 — Setup budgets, dual buffers, smarter Can I buy (PR6)

- **Schema v16**: `accounts.safety_buffer` (per-account)
- **IFPP**: effective buffer = max(total floor, sum of per-account buffers)
- **Setup budgets**: seed-from-history review + edit amounts before continue
- **Setup buffers**: total cash floor + per-account reserves
- **Can I buy?**: per-cash-account options after buffers; recommend best cash account or rewards-matched card
- APIs: `/api/setup/budgets`, `/budgets/apply`, `/buffers`

## 1.0.43 — Setup recurring finalize + categorize confirm (PR5)

- **`GET/POST /api/setup/recurring`** — remaining bill patterns; accept selected
- **`GET /api/setup/categorize`**, **`POST .../auto`**, **`POST .../confirm`** — high-conf auto + top payee chips
- Confirm creates category rule (“apply to all like this”)
- Wizard: recurring checklist; categorize queue (cap ~20) with skip payee
- Cap confirm friction; rest stays for Sort charges

## 1.0.42 — Setup discover liabilities (PR4)

- **Schema v15**: `payment_option` + `payment_fixed_amount` on accounts (minimum / fixed / statement / interest_saving)
- **`GET /api/setup/discover`** — skim cash outflows for cards, loans, bills, recurring investments
- **`POST /api/setup/discover/apply`** — create accounts + scheduled payments/debits from confirmations
- **`POST /api/accounts/{id}/payment-option`** — set card/loan payment plan
- **Wizard**: Discover checklist with payment plan dropdowns; investments → recurring budget debits
- Default card plan: **interest_saving** (IFPP north star)

## 1.0.41 — Setup CSV cash loop (PR3)

- **`GET /api/setup/cash`** — cash accounts + import status + bank guides
- **`POST /api/setup/cash-account`** — checking / savings / money market (+ primary IFPP flag)
- **Wizard CSV path**: **+ Cash account** → type → nickname/bank → download guide (login link + steps) → pick CSV/OFX import
- Loop: add another cash account or **Next** toward discover
- First checking becomes Safe-to-spend primary cash

## 1.0.40 — Plaid BYOK secrets + app Link + AI keys (PR2)

- **Local secrets store** (`~/.HonestSpend/secrets.json`, Windows DPAPI for secret fields)
- **`POST/GET/DELETE /api/plaid/credentials`** — client id + secret + env; hot-reload (no restart)
- **`POST/GET/DELETE /api/ai/credentials`** — Grok (xAI), OpenAI, Anthropic, custom
- **Plaid trial limit** — track Items n/10; block new Link at cap; status shows remaining
- **App-owned Link** — wizard opens `/static/plaid-link.html` after keys saved
- **Wizard steps** — plaid_keys, plaid_link, ai_keys (after Plaid path)
- Signup link: https://dashboard.plaid.com/signup

## 1.0.39 — Smart setup wizard foundation (PR1)

- **Resumable setup state**: `setup_phase`, `setup_path`, payload (schema v14)
- **API**: `GET /api/setup/state`, `POST /api/setup/advance`, `POST /api/setup/complete`
- **Paths**: Plaid · CSV · Quick manual (manual fully works; other phases placeholders)
- **WinUI Get started**: progress bar, path choice, resume after restart
- Onboarding `needs_setup` follows wizard phase (not only empty accounts)
- Plan locked: BYOK secrets store, app-owned Link, +cash loop, card payment options, per-account buffers, multi-LLM keys after Plaid

## 1.0.38 — Can I buy? respects budget reserve

- **Pre-purchase cash path** uses **Safe to spend** (cash − period budget reserve), same as Home
- **`category_id`** on `POST /api/pre-purchase` → server `budget_check` + verdict `safe_budget_tight`
- WinUI Can I buy? passes category and shows reserve snapshot
- Response: `ifpp_snapshot.budget_reserve`, `safe_to_spend`

## 1.0.37 — Seed budgets from spend history

- **`POST /api/budgets/seed-from-history`**: create daily/weekly/monthly plans from top categorized spend (food→daily, gas→weekly, rent/util→monthly)
- **Budgets page**: “Seed from spend history” button
- **Home**: soft tip when you have categorized expenses but no budgets yet
- `only_if_empty` mode for safe one-tap onboarding

## 1.0.36 — Accept budget suggestions + Can I buy? cut re-check

- **Budgets**: one-tap **Accept** suggestion sets plan from long trailing average; auto-load suggestions; unbudgeted top-spend categories get proposals
- **Can I buy?**: applying a budget cut **auto re-checks** purchase
- `POST /api/budgets/suggestions/accept` wired in WinUI client

## 1.0.35 — Budget cuts apply + workweek settings + Can I buy? category check

- **Home / Budgets**: tap cut chips to apply (skip workdays, scale week, release month)
- **Settings**: budget reserve toggle, week start, workday checkboxes (Mon–Sun bitmask)
- **Can I buy?**: optional budget category; warns if over remaining; shows cut offers when tight
- Settings API: `budget_reserve_enabled`, `budget_week_starts_on`, `budget_workdays`

## 1.0.34 — Period budgets (daily / weekly / monthly)

- **Budget rules** per entity: daily (workdays), weekly, monthly — Excel Budget.xlsx parity
- **History suggestions**: trailing ~90 workdays / 24 weeks / 18 months (avg + median + trend)
- **Safe to spend** subtracts remaining budget reserve (no double-count)
- **Cuts**: skip N workdays, scale weekly remaining, release monthly — frees reserve
- **API**: `/api/budgets*`, status, suggestions, cuts
- **WinUI**: Full books **Budgets** page; Simple Home budget summary + cut chips
- Schema v13: `budget_rules`, `budget_adjustments`, reserve settings
- No carryover between periods (by design)

## 1.0.33 — Store license path + Simple Home polish

- **Store license**: MSIX/packaged client sets `FOS_LICENSE_ENFORCE=1`; `StoreLicenseService` checks Microsoft Store and posts to `POST /api/license/store`
- **Activate license** page: Restore Store purchase, open Store listing, clearer status, advanced clear
- **Home (Simple)**: license soft-gate InfoBar when commercial build needs purchase; clearer Safe to spend card and status line
- `PackageInfo` detects packaged vs unpackaged builds
- Docs: LICENSING store sync notes

## 1.0.32 — Commercial license stub (buy once, all clients)

- **Design:** `docs/LICENSING.md` — $49.99 lifetime personal, cross-store entitlement via publisher license
- **Engine:** `license_service` + `GET/POST /api/license*` (local `license.json`, 90-day grace, OSS default unlocked)
- **WinUI:** **Activate license** page; About links status + commercial note
- **Config:** `FOS_LICENSE_ENFORCE`, `FOS_LICENSE_SERVER_URL`, `FOS_LICENSE_GRACE_DAYS`, `FOS_LICENSE_ALLOW_DEV_KEYS`
- Dev keys `HS-DEV-LIFETIME` / `HS-TEST-LIFETIME` for local testing when enforce is off
- CI/CD site + Store submit workflows remain separate (`docs/CI_CD.md`)

## 1.0.31.2 — Store readiness: engine extract + listing docs

- **EngineBootstrap**: first-run extract of `engine-portable.zip` → `%LocalAppData%\HonestSpend\engine`
- Settings: **Install / repair engine**
- `prepare-engine-bundle.ps1` writes `dist/engine-portable.zip`; MSIX pack includes it
- Docs: PRIVACY, STORE_LISTING, STORE_CHECKLIST; `scripts/sync-version.ps1`

## 1.0.31.1 — MSIX packaging scaffold (Store track)

- `Package.appxmanifest` full-trust Desktop Bridge identity + certification notes
- `scripts/package-msix.ps1` builds `.msix` to `dist/msix/` (optional self-signed cert)
- `docs/MSIX.md` Partner Center checklist; INSTALL/clients README link Store vs GitHub paths
- Default `WindowsPackageType=None` for daily dev; MSIX only when packaging

## 1.0.31 — Plaid create honesty · unified trust · multi set_books

- **Plaid create**: missing `balances.current` → not IFPP cash (no phantom $0 Safe to spend)
- **Import**: one rule — file set bank bal **or** typed bal → trust Safe to spend (CSV/OFX/PDF)
- **Inbox**: multi-account set_books CTAs (not only enter); flush before archive
- **books_brief / ritual**: missing linked cash bal ranks above Sort; bank_ok incomplete
- **WinUI**: set_books queue after multi-cash inbox; filter ResultText after trust
- **Dogfood**: hard assert books == 6000 after trust

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

## 1.0.11 — Rename: HonestSpend → HonestSpend

- **Product name:** **HonestSpend**
- Full rename: docs, CLI (`honestspend`), WinUI `HonestSpend.WinUI`, packaging, tray/tasks
- Python import path stays `honestspend`; data dir stays `~/.HonestSpend` (FOS_*)
- Legacy CLI aliases: `HonestSpend`, `honestspend`, `HonestSpend`
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
- **Inbox drop folder** (`data_dir/inbox`) · `HonestSpend import-inbox` · `POST /api/import/inbox/process`
- Scheduled task **HonestSpend-ImportInbox** (daily 09:00) via register-tasks
- Filename → account nickname matching; processed CSVs archived

## 1.0.6 — Rename: HonestSpend → HonestSpend (working alpha)

- **Product name:** **HonestSpend** (working alpha at the time)
- Full rename: docs, CLI (`HonestSpend`), WinUI project `HonestSpend.WinUI`, packaging, tray/tasks
- Python import path stays `honestspend`; data dir stays `~/.HonestSpend`
- Legacy CLI alias `honestspend` still points at the same entry point
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
- **Staged backup restore**: writes `honestspend.db.next` + `.restore_pending`; applied on engine restart
- **Schema migrations** via `schema_meta` + versioned runner (`honestspend.migrations`)
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
