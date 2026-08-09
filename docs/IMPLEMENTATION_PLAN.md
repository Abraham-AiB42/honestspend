# LedgerRing — Full Implementation Plan

**Source:** End-to-end code review (2026-08) + PRODUCT.md constitution + product suggestions  
**Goal:** Close every high/medium gap, ship constitution completeness, then product ideas  
**Constraint:** Local-first, freeware, Windows-first client (WinUI), Python engine stays source of fiscal truth  
**Success:** User can open rarely, never bounce checking, never pay avoidable interest/fees, multi-entity books stay clear, daily path ≤2 minutes  

**Product dream (Excel + CK + QB + TT + Rocket Money jobs):** see **[`docs/DREAM_ROADMAP.md`](./DREAM_ROADMAP.md)** — H1 live books → H2 v1.0 → H3 platforms.  
Phases P0–P1 and packaging are largely **shipped** in v0.7.x; continue from **P2 / dream H1**.

This plan is **ordered by dependency and risk**. Do not skip P0 for feature work.

---

## 0. North-star acceptance (definition of done for “everything”)

When this plan is complete:

1. **Spendable is siloed by entity by default**, with an explicit Combined (owner) view.  
2. **Restore is safe** (no hybrid DB while engine holds connections).  
3. **Non-loopback requires API key** (or hard fail).  
4. **One primary desktop surface** (WinUI); web is Link/minimal only.  
5. **Fee + promo + red-day + uncategorized** land in digest with clear actions.  
6. **Reconciliation path** exists after Plaid/CSV.  
7. **Daily 2-minute mode** is the default home.  
8. **HTTP + engine integration tests** cover auth, IFPP scope, backup restore.  
9. **Installer story** (WinUI + `engine\`) is documented and scriptable.  
10. **Roadmap leftovers** (Mac/mobile/encryption/services) are either shipped as MVP or tracked with clear “later” gates.

---

## 1. Phases overview

| Phase | Name | Theme | Est. PR count |
|-------|------|--------|----------------|
| **P0** | Trust bar | Entity IFPP, safe restore, bind/auth, dual-UI | 6–8 |
| **P1** | Daily product | 2-min home, fee inbox, promo sink, entity switcher | 6–8 |
| **P2** | Live books | Reconcile, Plaid lifecycle, schedule sync | 5–7 |
| **P3** | Ops & quality | Lifespan, schema version, API tests, tray↔native | 5–6 |
| **P4** | Tax & multi-state | State settings, packet enrich, CPA pack | 4–5 |
| **P5** | Automation | Task Scheduler service, logon polish | 3–4 |
| **P6** | Packaging | Installer, release notes, MSIX optional | 3–4 |
| **P7** | Platform later | Mac/Linux client, mobile glance, encryption | 4–8+ |

**Parallelism:** Within a phase, PRs marked *independent* can fan out. Across phases, finish P0 before P1 home depends on entity IFPP; P2 depends on P0 auth stability.

---

## 2. Phase P0 — Trust bar (must ship first)

### P0-1 · Entity-scoped IFPP + group mode
**Problem:** `run_ifpp` aggregates all profiles → multi-entity cash looks spendable as one pool.  
**Work:**
- Extend `run_ifpp(session, profile_id=None, scope="entity"|"group")`.  
  - `entity` (default): filter accounts + scheduled by `profile_id` (required unless single profile).  
  - `group`: current combined behavior (owner-only capability later).  
- API: `GET /api/ifpp?profile_id=&scope=entity|group&mode=`.  
- Capital desk / digest / pre-purchase accept same scope (or inherit default).  
- WinUI Home: entity ComboBox + Combined toggle; persist last selection in LocalSettings.  
- Settings default: `ifpp_scope=entity` on AppSettings.  
**Tests:** unit tests with two profiles, different cash, assert silo vs group.  
**Depends on:** nothing  
**Acceptance:** Personal Spendable ignores AP checking; Combined equals sum of sensible cash rules.

### P0-2 · Safe backup restore
**Problem:** Overwrite live SQLite while connections open.  
**Work:**
- `POST /api/backup/restore/*` returns `requires_restart: true` and sets a flag file `data_dir/.restore_pending`.  
- Prefer: copy restored DB to `financial_os.db.next`, set flag, **refuse writes** until process restart.  
- On startup (lifespan): if `.db.next` exists, replace live DB atomically, clear flag.  
- WinUI Data page: wizard steps (confirm → restore → auto-restart engine → health ping).  
- Document: never restore while another client is writing.  
**Tests:** restore creates `.next` + flag; second request after simulated restart loads new data.  
**Depends on:** none (can parallel P0-1)

### P0-3 · Auth harden for non-local
**Problem:** No key = owner; Docker binds `0.0.0.0`.  
**Work:**
- Config: `FOS_REQUIRE_API_KEY=bool`, `FOS_ALLOW_NON_LOOPBACK=bool` (default false for key requirement when host ≠ loopback).  
- On startup: if bind host not in `127.0.0.1/::1/localhost` and no required key configured → log error and refuse to start (or force require key).  
- Middleware: if require key, missing/invalid key → 401 even for “desktop”.  
- Health stays open (or limited).  
- WinUI Settings: note when connecting to non-local URL.  
**Tests:** middleware unit + config startup guard.  
**Depends on:** none

### P0-4 · Dual UI policy (WinUI primary)
**Problem:** Tray opens web; WinUI is richer → drift.  
**Work:**
- Product decision locked in PRODUCT.md / DESIGN.md: **WinUI = primary desktop**.  
- Tray menu: “Open Spendable (browser)” demoted; prefer “Open API docs” + copy “run WinUI”.  
- Optional: tray leaves a small `show_ui` signal file that WinUI watches if already running (P3).  
- `web/`: keep `plaid-link.html` + minimal index that redirects messaging to “use desktop app”; freeze new features on full web app.  
**Depends on:** none

### P0-5 · Settings PATCH semantics
**Problem:** Partial updates can reset fields when clients send full defaulted models.  
**Work:**
- Prefer `PATCH /api/settings` with all fields Optional, only apply set fields.  
- Keep PUT as full replace **documented**.  
- WinUI uses PATCH for credit/fiscal partials where possible.  
**Tests:** PATCH one field leaves others unchanged.  
**Depends on:** none

### P0-6 · Schema version table
**Problem:** Silent ALTER swallows errors.  
**Work:**
- Table `schema_meta(version int)`.  
- Numbered migrations module (still SQLite-friendly).  
- Startup applies pending migrations with logging; fail hard on critical errors.  
**Depends on:** none (do early so later columns are clean)

### P0-7 · Changelog + version bump
- Bring CHANGELOG in line with actual WinUI surface.  
- Version `0.4.0` when P0 complete.

---

## 3. Phase P1 — Daily product (minimum time)

### P1-1 · Entity switcher shell
- Shared WinUI helper: `AppState.SelectedProfileId`, `Scope` (entity/group).  
- Apply to Home, Ledger, Bills, Can I buy?, Credit (debts filter), Tax packet default profile.  
**Depends on:** P0-1

### P1-2 · Daily 2-minute mode (default Home)
**Work:**
- Home has modes: **Daily** (default) | **Full cockpit**.  
- Daily shows only:  
  - Entity switcher  
  - Combined/entity Spendable  
  - Next red day  
  - Top 3 digest alerts (action buttons where possible)  
  - Capital desk headline (one line)  
  - “Done for now” / expand  
- Full cockpit: current cards (capital desk detail, promo list, digests).  
**Depends on:** P0-1, P1-1

### P1-3 · Fee inbox (“stupid fees”)
**Work:**
- Category or rule pack: `FEE_*` (late, interest, annual, NSF, foreign).  
- Scanner service: uncategorized + keyword heuristics → fee candidates.  
- API: `GET /api/fees/candidates`, `POST /api/fees/confirm`.  
- Digest alert `fee_detected`.  
- WinUI: section under Review or Daily.  
**Depends on:** categorizer rules

### P1-4 · Promo death clock → sinking fund bill
**Work:**
- When promo tracked: suggest/create `ScheduledItem` for monthly sink.  
- Digest urgency already exists — wire “Create sink bill” action.  
- Home Daily widget for critical promos.  
**Depends on:** scheduled API (exists)

### P1-5 · Can I buy? UX finish
- Remove raw JSON dump; structured verdict, recommended method, alternatives, IFPP snapshot.  
- Respect entity scope.  
**Depends on:** P0-1

### P1-6 · Intermix playbooks (MVP)
- Templates: Reimburse card from business, Quarterly distribution, Capital inject.  
- Each pre-fills kind + category guidance text (not legal advice).  
- Wizard page or dialog on Intermix.  
**Depends on:** intermix service (exists)

---

## 4. Phase P2 — Live books & bank truth

### P2-1 · Reconciliation model
**Work:**
- Per account: `books_balance`, `institution_balance` (from Plaid/manual), `last_reconciled_at`, `drift`.  
- API: `GET /api/reconcile`, `POST /api/accounts/{id}/set-balance` (already patchable — formalize).  
- WinUI Reconcile page: list drifts, one-tap “trust bank” / “trust books”.  
**Depends on:** P0-1 optional

### P2-2 · Plaid lifecycle
- List items: disconnect/remove, re-link, last_sync age badge.  
- Background sync interval setting (when engine up).  
- Map new Plaid accounts → default IFPP flags by type.  
- Error surfaces (ITEM_LOGIN_REQUIRED).  
**Depends on:** Plaid page (exists)

### P2-3 · CSV import harden
- Column mapping preview (date/amount/payee).  
- Dry-run counts before commit.  
- Sign convention helper (already bank/invert).  
**Depends on:** bank_csv

### P2-4 · Pending vs cleared
- Transaction status already exists — filter Ledger; IFPP uses cleared-only option.  
**Depends on:** P0-1

---

## 5. Phase P3 — Ops, quality, process lifecycle

### P3-1 · FastAPI lifespan
- Replace `on_event("startup")` with lifespan context.  
- Start/stop auto-backup loop cleanly.

### P3-2 · HTTP integration test suite
- TestClient fixtures with temp `FOS_DATA_DIR`.  
- Cases: health, ifpp scope, permissions 401/403, backup create/restore pending, credit profile, tax packet 404.  
**Depends on:** P0-2, P0-3

### P3-3 · Engine process manager polish
- Single-instance mutex for `serve` (port bind is enough; document).  
- BackendHost: log stderr to file under data_dir.  
- TrayHost: detect existing tray PID file.  
- On WinUI exit option: “Stop engine” vs leave running.

### P3-4 · Tray ↔ native show
- Named event / localhost `POST /api/ui/ping` unused; simpler:  
  - Second launch of WinUI with no args calls `ShowMainWindow` if single-instance;  
  - Or tray opens `lederring://show` protocol registered by WinUI.  
**MVP:** single-instance WinUI + activate existing window.

### P3-5 · Data dir change wizard
- Settings: set path → optional “copy DB now” → restart engine → verify system/info.

### P3-6 · Account archive
- Soft `archived_at` instead of hard delete; hide from IFPP; keep history.

---

## 6. Phase P4 — Tax & multi-state readiness

### P4-1 · Entity tax profile fields
- Profile: home state, filing status notes, multi-state flag.  
- Settings per profile JSON or columns.

### P4-2 · Tax packet enrich
- Group by state if tagged; export still “for CPA, not advice”.  
- Uncategorized count gate before “ready”.

### P4-3 · CPA pack one-click
- ZIP: tax packet + category rules export + read-only API token (cpa_viewer) with optional expiry field.  
- WinUI Tax page: “CPA pack”.

### P4-4 · Multi-state lite
- Manual allocation % table (user-entered) for packet notes — not full nexus engine.  
**Out of scope still:** automated sales tax filing.

---

## 7. Phase P5 — Automation without the UI open

### P5-1 · Windows Task Scheduler scripts
- `scripts/register-tasks.ps1`:  
  - Daily engine health + auto-backup (start serve if down, run backup, optional stop).  
  - Optional digests.  
- Document uninstall.

### P5-2 · Logon path complete
- Already: `--tray-only`.  
- Verify engine + tray + auto-backup loop without flashing UI.  
- Single-instance + second launch shows UI.

### P5-3 · Digest CLI for cron
- Exists; add Windows scheduled task example + exit codes documented.

---

## 8. Phase P6 — Packaging & release productization

### P6-1 · Installer narrative
- Documented path: `publish-winui.ps1` + `prepare-engine-bundle.ps1`.  
- Optional Inno Setup / WiX script generating one installer EXE.  
**MVP:** zip + README “Install” section is enough for freeware.

### P6-2 · Release workflow harden
- Existing `release.yml` on `v*`.  
- Add engine-less WinUI zip + separate “engine instructions”.  
- CHANGELOG automation note.

### P6-3 · MSIX optional track
- Signing cert process doc; not blocking freeware zip.

### P6-4 · First-run: force first backup after quick setup
- Onboarding complete → create_backup note=`post-setup`.

---

## 9. Phase P7 — Platform later (tracked, gated)

Only after P0–P3 stable.

| Item | MVP approach | Gate |
|------|----------------|------|
| Mac/Linux desktop | Same API + thin native or official web limited | P0 dual-UI policy revisited |
| Mobile glance | Read-only Spendable + Can I buy over Tailscale/localhost tunnel | Auth P0-3 |
| DB encryption | SQLCipher or OS DPAPI-wrapped file | Backup/restore rewrite |
| Windows Service | NSSM / native service wrapping `serve` | P5 tasks first |
| QuickBooks/OFX export | Export modules | Tax packet patterns |
| Community COA packs | JSON install | Seed stability |

---

## 10. Product ideas mapped into plan

| Suggestion | Plan item |
|------------|-----------|
| Daily 2-minute mode | P1-2 |
| Entity switcher | P0-1, P1-1 |
| Promo sink fund | P1-4 |
| Fee inbox | P1-3 |
| Restore wizard | P0-2 |
| CPA pack | P4-3 |
| Scheduled engine/backup | P5-1 |
| Reconciliation | P2-1 |
| Intermix playbooks | P1-6 |
| Encrypted vault | P7 |
| Installer | P6-1 |
| Multi-platform | P7 |
| Export ecosystem | P7 |
| Onboarding + first backup | P6-4 |

---

## 11. PR Plan (execution order)

Each PR should be independently reviewable and keep `pytest` green + WinUI build green.

### Wave A — P0 Trust (serial where noted)

| PR | Title | Depends | Notes |
|----|--------|---------|--------|
| **PR-A1** | Schema version + migration runner | — | Foundation |
| **PR-A2** | Entity-scoped IFPP + API query params | A1 | Core fiscal fix |
| **PR-A3** | Safe restore (`.db.next` + startup apply) | A1 | Core data fix |
| **PR-A4** | Require API key when non-loopback | A1 | Security |
| **PR-A5** | PATCH settings semantics | A1 | API hygiene |
| **PR-A6** | WinUI entity switcher + Home scope | A2 | Client |
| **PR-A7** | WinUI restore wizard | A3 | Client |
| **PR-A8** | Dual-UI policy + tray/web freeze | — | Docs + tray |
| **PR-A9** | CHANGELOG 0.4.0 | A2–A8 | Release cut |

### Wave B — P1 Daily

| PR | Title | Depends |
|----|--------|---------|
| **PR-B1** | Daily 2-minute Home mode | A6 |
| **PR-B2** | Fee scanner + digest + Review UI | A2 |
| **PR-B3** | Promo sink scheduled item action | A6 |
| **PR-B4** | Can I buy? UX cleanup | A6 |
| **PR-B5** | Intermix playbooks | — |

### Wave C — P2 Live books

| PR | Title | Depends |
|----|--------|---------|
| **PR-C1** | Reconcile API + page | A6 |
| **PR-C2** | Plaid disconnect/reauth/sync age | — |
| **PR-C3** | CSV mapping preview | — |
| **PR-C4** | Pending/cleared IFPP option | A2 |

### Wave D — P3 Quality

| PR | Title | Depends |
|----|--------|---------|
| **PR-D1** | FastAPI lifespan | — |
| **PR-D2** | HTTP integration tests | A2,A3,A4 |
| **PR-D3** | Single-instance WinUI + show window | A8 |
| **PR-D4** | Tray PID + engine log file | — |
| **PR-D5** | Data dir copy wizard | A3 |
| **PR-D6** | Account archive | A1 |

### Wave E — P4 Tax

| PR | Title | Depends |
|----|--------|---------|
| **PR-E1** | Profile tax geo fields | A1 |
| **PR-E2** | Tax packet readiness gate | E1 |
| **PR-E3** | CPA pack export + token | A4 |
| **PR-E4** | Multi-state allocation notes | E1 |

### Wave F — P5 Automation

| PR | Title | Depends |
|----|--------|---------|
| **PR-F1** | register-tasks.ps1 auto-backup | A3 |
| **PR-F2** | Logon tray-only E2E doc + fixes | D3 |
| **PR-F3** | Digest scheduled task example | — |

### Wave G — P6 Package

| PR | Title | Depends |
|----|--------|---------|
| **PR-G1** | Install README + zip layout | — |
| **PR-G2** | Optional Inno/WiX installer | G1 |
| **PR-G3** | Post-setup auto-backup | A3 |
| **PR-G4** | Release workflow polish | G1 |

### Wave H — P7 Later (separate roadmap)

| PR | Title | Gate |
|----|--------|------|
| **PR-H*** | Encryption / Service / Mobile / Mac | After 0.5 stable |

---

## 12. Key decisions (for implementers)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Default IFPP scope | **Entity** | PRODUCT silo default |
| Combined view | Explicit toggle, owner | Prevent accidental commingling |
| Restore | Staged file + restart | SQLite + open connections |
| Auth | Open on loopback; strict off-loopback | Desktop trust vs Docker |
| Primary UI | **WinUI** | Native Windows first |
| Web | Plaid Link + stub | Stop dual maintenance |
| Migrations | Versioned runner | Replace silent ALTER-only |
| Daily mode | Default Home | Minimum user time |
| Fees | Heuristic + rules, not bank fee APIs | Practical |
| Multi-state | Notes + allocation, not filing | Honesty |
| Packaging | Zip+engine first, MSIX later | Freeware velocity |

---

## 13. Testing strategy

| Layer | Target |
|-------|--------|
| Unit | IFPP silo, restore staging, fee heuristics, migrations |
| HTTP | Auth, ifpp params, backup restore, permissions |
| WinUI | Manual checklist per PR; optional later UI tests |
| E2E script | `scripts/smoke-e2e.ps1`: serve → setup → backup → ifpp → restore pending |
| CI | Existing pytest matrix; add HTTP tests to same job |

**Do not merge P0 without:** pytest green + `dotnet build` WinUI x64.

---

## 14. Documentation updates (per wave)

- [ ] PRODUCT.md — mark silo default implemented; dual-UI policy  
- [ ] DESIGN.md — IFPP scope, restore, auth  
- [ ] CLIENTS.md — WinUI primary; web limited  
- [ ] README.md — install + entity Spendable + security note  
- [ ] CHANGELOG — every wave  

---

## 15. Suggested milestone tags

| Tag | Includes |
|-----|----------|
| **v0.4.0** | Wave A complete (trust bar) |
| **v0.4.1–0.4.4** | Waves B–E (daily, live books, ops, tax) |
| **v0.4.5** | Wave F (Task Scheduler + logon automation) |
| **v0.5.0** | Wave G packaging (package-release + INSTALL + post-setup backup) — **done** |
| **v1.0.0-local** | P7 later (encryption, multi-platform); local Windows product substantially complete |

---

## 16. Explicitly deferred (do not sneak into P0)

- Full sales tax / multi-state filing engine  
- Bureau credit APIs (impossible / not offered)  
- Payroll connectors  
- Investment product  
- Kids/family products  
- Affiliate card marketplace  
- Perfect concurrent multi-device SQLite on OneDrive (document as unsupported)

---

## 17. How to execute this plan

1. **Start at PR-A1** (migrations) — unblocks clean schema work.  
2. **A2 + A3 in parallel** after A1 (different code paths).  
3. Land **A6/A7** so WinUI matches engine trust fixes.  
4. Cut **v0.4.0**.  
5. Then B → C → D in order; E can start after A1.  
6. F/G when daily use is tolerable.  
7. Revisit H only with a separate design pass.

**Orchestration:**  
- Single engineer: A1→A2→A3→A4→A6→A7→A5→A8→A9 then B1…  
- Multi: after A1, parallel A2/A3/A4/A5/A8; then A6/A7.

---

## 18. Open questions (resolve before or during P0)

1. **Combined Spendable default for single-profile users?**  
   - Recommendation: if only one profile has cash accounts, auto entity; if multiple have cash, force explicit Combined toggle off by default.

2. **Should digest/capital desk always follow Home entity selection?**  
   - Recommendation: yes, global AppState scope.

3. **After restore, auto-kill engine child from WinUI?**  
   - Recommendation: yes in restore wizard.

4. **Web app: delete or freeze?**  
   - Recommendation: freeze + banner; delete index complexity later.

---

## 19. Tracking

Create GitHub milestones `v0.4` … `v0.7` and issues from PR titles above, labels:

- `p0-trust` `p1-daily` `p2-live` `p3-ops` `p4-tax` `p5-auto` `p6-ship` `p7-later`  
- `engine` `winui` `docs` `security`

---

*This plan supersedes ad-hoc “keep going” feature drops for prioritization. Implementation should still ship vertical slices (API + test + WinUI) per PR where possible.*
