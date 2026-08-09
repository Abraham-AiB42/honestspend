# Plan: Realize the Dream

**Product:** Floatpile (financial-os)  
**Baseline:** **v0.9.x** feature tags · product maturity **~65% dream (~0.6.5-class)** — see [`VERSIONING.md`](./VERSIONING.md)  
**Schema:** **v10**  
**1.0:** Only after [`RC_1.0.md`](./RC_1.0.md) — do **not** treat 0.9 as “almost 1.0.”  
**Dream:** One app that *does the job* of Excel-nerd control + Credit Karma awareness + QuickBooks books hygiene + TurboTax handoff + Rocket Money daily simplicity — without becoming five mediocre clones.

**Constitution (non-negotiable):** local-first freeware · never bounce checking · no dumb fees/interest · intentional 0% float · options + why · fiscal #1 · credit #2 · open rarely · **no** bureau marketplace · **no** payroll product · **no** trading product · **no** e-file as core product.

---

## 0. Dream definition (what “done” means)

Floatpile is “best of all worlds” when a real household + side hustle can:

| Job | User outcome | Replaces |
|-----|--------------|----------|
| **J1 Daily safety** | Open rarely; always know Safe to spend + one next action | Rocket Money core |
| **J2 Never get screwed** | No bounce, no accidental APR, fees surfaced with fix path | Rocket + CK *discipline* |
| **J3 Credit without theater** | Educational health + util/promo/debt plan; never prioritize score over cash | Credit Karma *awareness* |
| **J4 Multi-entity books** | Personal / Business / Child silos; clean intermix; CPA-readable export | QuickBooks Lite |
| **J5 Tax handoff** | Vault honest Spendable; annual packet “ready”; CPA pack | TurboTax *prep* (not e-file) |
| **J6 Nerd control** | Full books, simulate, rules, import memory, audit, APIs | Excel *for money* |

**Not done when:** five brand clones, marketplace loans, full e-file, or depth that breaks Simple mode.

### North-star score target by horizon

| Horizon | Feature tags | Dream completion (est.) | Honest label |
|---------|--------------|-------------------------|--------------|
| **Past** | 0.6–0.7 | ~40–50% | Simple + multi-entity spine |
| **Now** | **0.9.x** | **~65%** | **0.6.5-class** on 0→1.0 dream scale |
| **H1** | 0.8.x | ~55–65% | Live books + month-close |
| **H2 → 1.0** | **1.0.0 only after RC** | ~70–75% | “I live here” + dogfood |
| **H3** | 1.x–2.0 | ~85% | Platforms + nerd tools |
| **Never** | — | — | Full Intuit/CK marketplace parity |

---

## 1. Architecture that makes the dream possible

Keep the **one fiscal engine** (Python/SQLite FastAPI). Clients never re-implement math.

```text
┌─────────────────────────────────────────────────────────┐
│  Surfaces                                                │
│  Simple (daily) │ Full books │ Glance │ Tray │ CLI       │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP + X-API-Key
┌───────────────────────────▼─────────────────────────────┐
│  Engine :7420                                            │
│  IFPP · never-neg · rescue · capital desk · home/simple  │
│  books · credit · tax · fees · plaid · backup            │
└──────────────────────────────────────────────────────────┘
```

**Rule:** Every dream feature ships as (1) pure service + tests, (2) `home/simple` or Full API, (3) Simple CTA only if it serves open-rarely.

**Dual-mode forever:**

| Mode | Job |
|------|-----|
| **Simple** | Safe to spend · Do this next · 3-min check · Add wizards · Can I buy? · Sort charges · bank/CSV entry |
| **Full books** | Ledger, reconcile, rules, credit desk, tax vault/packet, users, intermix, import depth |

---

## 2. Pillar strategy (steal the job, not the brand)

| Pillar | Steal | Explicitly reject | Floatpile expression |
|--------|-------|-------------------|------------------------|
| **Excel nerd** | Explainability, what-if, multi-entity, rules, import memory | Live grid as primary UX | Full books + simulate + capital desk + APIs |
| **Credit Karma** | Util, “what if,” payoff order, promo urgency | Soft-pull marketplace, ads | Educational score + debt/promo/autopay |
| **QuickBooks** | Bank feed → books, reconcile, COA, CPA export | Invoices, payroll, inventory | Ledger + Plaid/CSV + reconcile + intermix |
| **TurboTax** | Readiness, set-aside, packet, multi-state fields | E-file engine | Tax vault + packet + CPA pack |
| **Rocket Money** | Bills, fees, simple daily UI, open rarely | Cancel-for-you marketplace | Simple Home + fee scan + scheduled + Glance |

---

## 3. Baseline inventory (v0.7.1) — already shipped

### Strong (do not rebuild)
- IFPP silo/group, never-neg gate, rescue, pre-purchase, simulate  
- Multi-entity Personal/Business/Child + intermix kinds  
- Capital desk + home/simple + wealth after safety + 3-minute check  
- Credit educational model, debt plan/compare, promo clock/sink, autopay  
- Ledger, void, transfer/payment match, CSV + presets, Plaid, reconcile  
- Tax vault, packet, CPA pack, multi-state profile fields  
- Fee scan, digest, Glance, tray, multi-user keys, encrypted backup  
- Simple/Full WinUI, first-run, money wizards, grade-A package scripts  

### Gaps that block the dream (ordered)

1. **Live books as default habit** — bank sync reliability, post-import 3-min path  
2. **Month/period close ritual** — guided books hygiene (QB job)  
3. **Subscription/bill intelligence** — detect + manage without cancel marketplace  
4. **Reports that matter** — cashflow by entity, tax readiness dashboard (not 50 GAAP reports)  
5. **Scenario sandbox** — Excel what-if productized  
6. **Mobile parity** — Glance → progressive web / read-write MVP  
7. **Nerd power tools** — audit UI, rule builder, optional grid  

---

## 4. Roadmap by horizon

### H0 — Stabilize the beachhead (v0.7.x maintenance)

**Goal:** Protect A grade; no regressions on Simple path.

| Work | Why |
|------|-----|
| WinUI dogfood checklist + fix P0 bugs | Trust |
| Keep `verify-grade-a.ps1` green on every release | Ship bar |
| Package zip smoke on clean machine | Install story |
| Docs: one-pager “who this is for / not for” | Positioning |

**Exit:** v0.7.x is boring-reliable.

---

### H1 — “I open this weekly and never bounce” (v0.8 – v0.9)

**Theme:** Rocket Money job + live books path. **Target dream: ~55–60%.**

#### Epic H1-A · Live books loop (Plaid/CSV → Safe to spend)
| PR | Deliverable | Depends |
|----|-------------|---------|
| H1-A1 | Post-sync **import brief**: new txns, pending $ impact, uncategorized count → Simple CTAs | — |
| H1-A2 | **Recurring detector**: suggest bill/sub from history (name + cadence + amount) | A1 |
| H1-A3 | Simple **“Money in”** wizard: link bank / CSV / manual (one hub, already partial) | — |
| H1-A4 | Plaid re-auth + stale sync loud on Home (digest + 3-min step) | A1 |
| H1-A5 | Pending-aware banners always plain language; one-tap “review pending” | — |

**Acceptance:** After CSV or Plaid sync, user reaches “Safe to spend still honest” in ≤5 minutes without Full books.

#### Epic H1-B · Month-close wizard (QuickBooks job, Simple shell)
| PR | Deliverable |
|----|-------------|
| H1-B1 | `GET /api/home/close-checklist` (or extend `three_minute_check` → period mode) |
| H1-B2 | WinUI **Close the month** flow: fees · Sort charges · promo · tax vault · reconcile drift · backup |
| H1-B3 | Mark period closed (schema v10: `period_closes` table optional) |

**Acceptance:** SMB user runs month-close monthly without CPA software.

#### Epic H1-C · Never-get-screwed automation
| PR | Deliverable |
|----|-------------|
| H1-C1 | Autopay desk applies to capital desk + Home CTA when due week |
| H1-C2 | Fee inbox as first-class Simple surface (or Home card) with confirm/ignore |
| H1-C3 | Promo balloon: monthly set-aside scheduled item one-tap from Home |

**Acceptance:** Promo and fee paths never require hunting Credit page first.

#### Epic H1-D · Entity money for humans
| PR | Deliverable |
|----|-------------|
| H1-D1 | Simple playbooks: reimburse · pay yourself · kid allowance (guided, not Intermix form dump) |
| H1-D2 | Business tax set-aside default soft-on when Business entity exists (already partial) |

**Exit H1:** v0.9 — “weekly OS for household + side hustle.”

---

### H2 — “I live here” 1.0 (v1.0)

**Theme:** Replace the *jobs* of four apps for target user. **Target dream: ~70–75%.**

#### Epic H2-A · Reports that matter (not QB Enterprise)
| Report | Purpose |
|--------|---------|
| Cash flow by entity (30/90d) | Excel + Rocket |
| Fee year + interest avoided | Motivation / trust |
| Debt payoff timeline compare | CK-like without bureau |
| Tax readiness score + missing lines | TT prep |
| Net worth **cash+debt only** (optional investments manual) | Honest, not brokerage |

Ship as `GET /api/reports/*` + Full books Reports page + 1–2 cards on Simple Home.

#### Epic H2-B · Tax handoff year
| PR | Deliverable |
|----|-------------|
| H2-B1 | Annual checklist wizard (W-2 vs Sch C, vault funded, packet export) |
| H2-B2 | Estimated quarterly calendar for business income (reminders in digest) |
| H2-B3 | CPA pack v2: README for accountant + entity silos zip structure |

**Still out:** e-file, full form engine.

#### Epic H2-C · Credit as fiscal secondary only
| PR | Deliverable |
|----|-------------|
| H2-C1 | Credit “health board” rewrite: plain language, util first, score secondary |
| H2-C2 | What-if util scenarios tied to Safe to spend (don’t wreck cash for score) |
| H2-C3 | Self-reported score import UX (screenshot/manual) — never fake bureau |

#### Epic H2-D · Scenario sandbox (Excel dream)
| PR | Deliverable |
|----|-------------|
| H2-D1 | Named scenarios: extra outflow schedule, income change, promo end |
| H2-D2 | Compare baseline vs scenario on Safe to spend + red day + interest |
| H2-D3 | Save/load scenarios (schema) |

#### Epic H2-E · Productization 1.0
| PR | Deliverable |
|----|-------------|
| H2-E1 | One-click installer path polished (Inno default in release.yml) |
| H2-E2 | In-app “What’s new” + version check (optional GitHub releases) |
| H2-E3 | CONTRIBUTING + public one-liner locked |

**Exit H2:** **v1.0** tag — marketing: *Liquidity OS for people who refuse dumb money.*

---

### H3 — Power + platforms (v1.x – v2.0)

**Theme:** Excel power tools + multi-device without cloud multi-writer fantasy. **Target dream: ~85%.**

#### Epic H3-A · Nerd tools (Full books only)
- Rule builder UI (merchant → category, learn on accept)  
- Audit log browser (already API)  
- Optional transaction **grid** view (virtualized list with bulk categorize)  
- Custom debt rank + opportunity hurdle advanced editor  
- Export: OFX/QBO-friendly CSV packs  

#### Epic H3-B · Multi-platform **native clients** (no PWA)
- **Client-first forever** — WinUI is primary; no Progressive Web App product path  
- Glance HTML remains a **thin emergency shell** for Mac/Linux/phone, not the roadmap  
- Future: native Mac/Linux desktop (Swift/Qt/Avalonia — TBD), then mobile **native** if ever  
- Tailscale/docs for remote home-lab access to the **engine + native client**  

#### Epic H3-C · Automation OS (Windows client)
- Task Scheduler packs as first-class **WinUI Settings** UI (digest, backup, plaid sync)  
- Tray: Safe to spend + toast + **open WinUI** (not browser)  
- Optional: engine as Windows service (after tray path rock solid)  

#### Epic H3-D · Collaboration (local)
- Bookkeeper role daily path (import + categorize only)  
- CPA viewer link expiry + pack download  
- Still **no** live multi-writer cloud SQLite  

**Exit H3:** Platform-complete freeware flagship without SaaS.

---

## 5. Explicit non-goals (forever or until constitution changes)

| Non-goal | Why |
|----------|-----|
| Credit card / loan marketplace | Constitution; conflict of interest |
| Full e-file | TurboTax-scale liability/scope |
| Payroll product | Constitution |
| Brokerage trading | Constitution |
| Affiliate “cancel Netflix for you” marketplace | Not liquidity OS |
| Multi-writer cloud SQLite | Data corruption; use encrypted snapshots |
| Replacing Excel for non-money work | Wrong product |
| **PWA / “browser as the app”** | **Client-first** — native WinUI (then native Mac/Linux); Glance is fallback only |

---

## 6. PR execution order (near-term stack)

Recommended serial/parallel plan for the next 2–3 releases:

```text
v0.8  Live books + import brief + recurring detect + Home bank health
        ├─ H1-A1 import brief
        ├─ H1-A2 recurring detector
        ├─ H1-A4 plaid health
        └─ H1-C2 fee card on Home

v0.9  Month-close + intermix playbooks + promo one-tap
        ├─ H1-B1/B2 close wizard
        ├─ H1-C3 promo set-aside CTA
        └─ H1-D1 simple intermix playbooks

v1.0  Reports + tax year + scenarios + installer polish
        ├─ H2-A reports MVP
        ├─ H2-B tax year
        ├─ H2-D scenarios
        └─ H2-E productization
```

**Quality gate every release:**
```powershell
.\scripts\verify-grade-a.ps1
# + manual WinUI dogfood: first-run → buy → sort → close checklist
```

**Schema bumps (planned):**
| Version | Feature |
|---------|---------|
| v10 | Period closes / month-close markers (if needed) |
| v11 | Saved scenarios |
| v12 | Recurring detection state / subscription flags |

Prefer **derive** recurring from data before schema when possible.

---

## 7. Workstream ownership (how to execute without thrash)

| Stream | Owns | Cadence |
|--------|------|---------|
| **Engine** | Services, APIs, tests, schema | Every PR |
| **Simple surface** | Home, wizards, Glance copy, tray | Parallel after API |
| **Full books** | Ledger, reconcile, tax, users | After Simple CTAs stable |
| **Ship** | package-release, Inno, verify-grade-a, RELEASE notes | End of each minor |

**Rule:** No Full-books-only feature without a Simple *signal* (alert, 3-min step, or Do this next) if it affects safety.

---

## 8. Metrics (how we know the dream is landing)

| Metric | Target |
|--------|--------|
| Time first-run → Safe to spend | ≤2 min |
| Time post-import → honest Safe to spend | ≤5 min |
| Daily open time when “all clear” | ≤60 sec (3-min check green) |
| Month-close guided time | ≤10 min |
| Simple path raw `#id` / IFPP jargon | Zero |
| `verify-grade-a` | Always green |
| Northstar e2e | Expand with live-books + close-checklist cases |

---

## 9. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Scope creep into Intuit | Non-goals list; every epic maps to a *job* not a brand |
| Simple mode bloat | Feature goes Full first; promote to Simple only if open-rarely |
| Plaid flaky / paid | CSV first-class forever; Plaid optional excellence |
| Tax advice liability | Educational disclaimers; export not advice; no e-file |
| Dual-client drift | home/simple is single source of daily truth for WinUI + Glance |
| Solo maintainer bandwidth | Ship vertical slices (v0.8 thin) not big-bang 1.0 |

---

## 10. Documentation deliverables (with code)

| Doc | Purpose |
|-----|---------|
| `docs/DREAM_ROADMAP.md` | This plan, durable in-repo (copy on execute) |
| `docs/SIMPLE_MODE.md` | Keep language table current |
| `docs/RELEASE_x.y.z.md` | Each minor |
| PRODUCT.md | Constitution only — change rarely |

Update IMPLEMENTATION_PLAN.md phases P2–P7 to point at this dream plan as the product north document.

---

## 11. Immediate next actions (when plan approved)

1. **Persist plan** → `docs/DREAM_ROADMAP.md`  
2. **Start v0.8** with H1-A1 (import/sync brief into home/simple + WinUI card)  
3. Expand `_northstar_e2e.py` with post-setup import-path assertions  
4. Dogfood WinUI first-run + CSV import on a real household DB  

Do **not** start H3 mobile natives or scenario schema until H1 live-books loop is green.

---

## 12. Success narrative at v1.0

> Floatpile is the local liquidity OS: Safe to spend you can trust, multi-entity books a CPA can open, tax handoff without TurboTax theater, credit discipline without Credit Karma ads, and Excel-grade what-ifs without living in a spreadsheet. Open rarely. Never bounce. Never pay dumb interest by accident.

That’s the dream—**realized as one product**, not five brands.

---

## Appendix A — Capability matrix (now → 1.0)

| Capability | 0.7.1 | 0.9 | 1.0 |
|------------|-------|-----|-----|
| Safe to spend + Do this next | ✓ | ✓ | ✓ |
| 3-min / month-close | 3-min only | + month-close | polished |
| Live import brief | partial | ✓ | ✓ |
| Recurring/sub detect | — | ✓ | ✓ |
| Fee/promo Simple CTAs | partial | ✓ | ✓ |
| Intermix playbooks Simple | — | ✓ | ✓ |
| Reports by entity | — | basic | ✓ |
| Tax year wizard | vault/packet | + reminders | ✓ checklist |
| Scenarios | simulate ad-hoc | — | ✓ named |
| Installer | zip + Inno script | ✓ | polished |
| Extra platforms | Glance read-only | native client later | never PWA |

## Appendix B — Mapping to old IMPLEMENTATION_PLAN phases

| Old phase | Dream plan home |
|-----------|-----------------|
| P0–P1, P6 | **Done** (trust, simple daily, package) |
| P2 Live books | **H1-A** |
| P3 Ops | Continuous + H3-C |
| P4 Tax | **H2-B** |
| P5 Automation | **H3-C** |
| P7 Platform | **H3-B** |
