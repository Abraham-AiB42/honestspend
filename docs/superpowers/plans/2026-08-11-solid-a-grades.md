# Solid A grades — Engine · Simple · Full

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement phase-by-phase. Tasks use checkbox (`- [ ]`) syntax.

**Goal:** Move HonestSpend from review grades **Engine A− · Simple A− · Full B+** to a **solid A in all three**, without bloating Simple or inventing new product lines.

**Architecture:** Keep one cash spine (Safe to spend) and one pay-policy spine (`autopay_policy`). Collapse dual models where they still create honesty drift. Make Full progressive disclosure, not a second product.

**Tech stack:** Python FastAPI/SQLite (`honestspend`), WinUI 3, pytest, existing SIMPLE_MODE / STATEMENT_CYCLES contracts.

**Current baseline (post trust-bar + polish, 2026-08-11):**
| Category | Grade | Why not A yet |
|----------|-------|----------------|
| **Engine** | A− | Dual schema (`payment_option` / `autopay_policy`); soft-until math ≠ STS base; void/recompute asymmetry; match heuristics residual |
| **Simple** | A− | Soft until often invisible under budgets; risk day ISO dates; Coming up truncated list; doc/engineer “IFPP” still in surface table |
| **Full** | B+ | Credit is one mega-scroll desk; dual columns remain; payroll `#id` copy; no progressive disclosure |

---

## Global constraints

- **Never** reintroduce Home cash dual stories (runway busy strip, second primary $).
- **Never** show IFPP / horizon / runway jargon on Simple (or Full default copy).
- **Never-neg** and mark-paid match-before-post stay ship-blockers if regressed.
- Local-first freeware; no new cloud deps.
- TDD for engine honesty; WinUI dogfood for copy/density.
- Prefer delete/collapse dual models over “sync harder.”

---

## Definition of solid A (exit criteria)

### Engine = A when

1. **One policy field** is authoritative for credit pay amount (wizard maps in; no reverse-drift footguns).
2. **Soft-until base** is the same spendable cash as STS (or explicitly labeled “before budgets” with identical buffer math).
3. **Void / reverse** of card payments cannot desync `Card payment ·` next_date (recompute symmetry with mark-paid).
4. **Match path** has digit-safe tests + documented false-positive rate controls.
5. Focused honesty suite green: mark-paid, import advance, promo effective balance, statement cycle, never-neg.

### Simple = A when

1. Home always teaches **one** primary cash number; secondary lines only appear when they add information.
2. Soft until either **shows usefully under budgets** (post-reserve base) or is removed entirely from Simple (product pick — prefer post-reserve).
3. Risk day uses **plain weekday dates** (same style as Coming up).
4. Coming up: full-window access without dumping Full mode (expand / “Show all in window”).
5. Language table enforced on Simple paths (spot-check: no IFPP/horizon in UI strings).

### Full = A when

1. Credit desk is **progressive disclosure** (Statement & payment first; promo / score / payoff / freeze collapsed by default or secondary pages).
2. **One pay-policy editor** only (no collapsed dual controls left “for later”).
3. Power copy is plain (no raw schedule `#id`s in success toasts).
4. Full Bills mark-paid + End + series steps feel complete; no dead dual autopay surfaces.
5. Full user can answer “what do I pay this card, when, from which cash?” in **one scroll section**.

---

## Phase map (do in order)

```
Phase 0  Ship hygiene          (optional parallel)  — version / commit / dogfood checklist
Phase 1  Engine single spine   → Engine A
Phase 2  Simple secondary UX   → Simple A
Phase 3  Full progressive desk → Full A
Phase 4  Grade gate            → re-review + dogfood sign-off
```

Phases 1–3 are sequential for honesty. Phase 0 can run anytime.

---

## Phase 0 — Ship hygiene (optional, does not change grades)

**Goal:** Branch is dogfoodable; Unreleased is not a mystery.

- [ ] Commit trust-bar + polish wave on `feature/statement-cycles` with clear message.
- [ ] Dogfood checklist (30–60 min): import CSV → Coming up advance; mark paid Home + Bills; card pay next_date +1 mo; never-neg warn dialog; soft until with/without budgets.
- [ ] Bump version only when ready for MSIX (not required for grade A).

**Exit:** Human can run daily Simple for a week without surprise double-posts.

---

## Phase 1 — Engine solid A

### Task 1.1: Soft-until base = post-reserve cash (or kill soft line)

**Why A−:** Soft starts from runway cash (buffer/tax only); STS subtracts budget reserve → soft often equals STS after cap and vanishes, teaching nothing under budgets.

**Decision (recommended):** Rebuild soft from **same cash as STS** (`cash` after budget reserve in `home_simple`), then subtract Coming up outflows, floor at 0, **do not** re-cap to STS if already same base (or cap only if soft would exceed STS due to pending).

**Files:**
- Modify: `src/honestspend/services/home_simple.py` — pass post-reserve cash into soft builder
- Modify: `src/honestspend/services/cash_runway.py` — `build_safe_until_window` accept `starting_cash` override
- Test: `tests/test_safe_until_cap.py`, `tests/test_safe_until_window.py`

**Steps:**

- [ ] **Step 1:** Failing test — household with $2000 cash, $500 budget reserve, $200 coming-up outflow → soft amount is `$1300` (2000−500−200), not capped hide of STS.

```python
def test_soft_until_uses_post_budget_cash(tmp_path, monkeypatch):
    # setup: cash 2000, buffer 0, budget remaining 500, one cash bill 200 in window
    home = build_home_simple(s, as_of=as_of, profile_id=p.id)
    assert Decimal(home["safe_to_spend"]) == Decimal("1500.00")  # or whatever STS is
    soft = Decimal(home["safe_until_window"]["amount"])
    # soft should be STS - (outflows already in STS? careful — document formula
    # Preferred formula: soft = max(0, sts - coming_up_outflows_not_already_in_sts)
    # SIMPLER product formula for A:
    # soft = max(0, post_reserve_cash - coming_up_outflow_total)
    # STS already ≈ post_reserve_cash after IFPP bills; if double-count risk, use:
    # soft = min(STS, max(0, post_reserve_start - window_outflows))
    assert soft < Decimal(home["safe_to_spend"]) or home["safe_until_window"]["shows_distinct"] is True
```

**Simpler chosen formula for A (implement this):**

```
post_reserve = STS base used on Home (already computed as `cash` in home_simple)
soft_raw = max(0, post_reserve - coming_up.outflow_total)
# If IFPP already reserved those bills in STS, soft_raw understates — then:
# soft = post_reserve  only when window outflows ⊆ IFPP schedule already deducted
# PRAGMATIC A: soft_amount = max(0, post_reserve - coming_up_outflows)
# and label "After bills in Coming up window" — accept soft ≤ STS always
# Hide soft only when abs(soft - STS) < 0.01 (unchanged UI)
```

- [ ] **Step 2:** Implement `starting_cash` optional override on `build_safe_until_window`.
- [ ] **Step 3:** `home_simple` passes `cap_at=cash` and `starting_cash=cash` (same post-reserve).
- [ ] **Step 4:** Tests green; soft can be **lower** than STS when window outflows remain.
- [ ] **Step 5:** Commit `fix: soft until uses post-reserve cash base`

---

### Task 1.2: Collapse dual policy to one authority

**Why A−:** `payment_option` (wizard) + `autopay_policy` (desk) with bidirectional sync is glue, not one model.

**Approach (YAGNI-friendly):**
1. **Authority:** `autopay_policy` is the only field Credit/cycle/recompute read.
2. **Wizard:** map discover options → write `autopay_policy` only (keep `payment_option` as deprecated alias for one release).
3. **API:** `set_account_payment_option` becomes thin wrapper that only sets policy map.
4. **Stop reverse-sync complexity** once nothing reads `payment_option` for amounts.

**Files:**
- Modify: `src/honestspend/services/autopay.py`, `setup_discover.py`, `api/app.py`
- Modify: tests that assert `payment_option == "statement"`
- Docs: `STATEMENT_CYCLES.md`, `SIMPLE_MODE.md` one-liner “pay policy = autopay_policy”

**Steps:**

- [ ] Grep all `payment_option` reads in services (not migrations).
- [ ] Replace amount/policy decisions with `autopay_policy` / `ensure_autopay_policy_from_payment_option` only at edges.
- [ ] Wizard continues to accept payment_option strings but only persists policy.
- [ ] Tests: discover credit → `autopay_policy=statement`; cycle PUT min → policy min; `none` sticky.
- [ ] Commit `refactor: autopay_policy is sole pay-policy authority`

**Exit Engine policy:** no feature can show different “what to pay” from two fields.

---

### Task 1.3: Void/reverse recompute symmetry

**Why A−:** mark-paid uses `recompute=False` then restore next_date; void may recompute and rewrite schedule.

**Files:**
- Modify: `src/honestspend/services/account_balance.py` — `reverse_amount_on_account(..., recompute=True)`
- Modify: void path in `txn_void.py` / API
- Test: void card payment after mark-paid → schedule next_date restored reasonably (or recompute once intentionally)

**Steps:**

- [ ] Test: mark paid card (new legs) → next_date D2; void cash or card txn → schedule either ends or recomputes to consistent future due, **not** double-step garbage.
- [ ] Implement: after void of transfer-pair card payment, call `recompute_card_payment_schedule` once (full recompute OK on void).
- [ ] Commit `fix: void card payment recompute once cleanly`

---

### Task 1.4: Match hardness tests

**Files:**
- Modify: `tests/test_schedule_mark_paid.py`

- [ ] Test digit-safe: schedules for `card_account_id=1` vs `12` never cross-match.
- [ ] Test same-amount different payees already exists — keep.
- [ ] Commit `test: digit-safe card_account_id advance filter`

**Phase 1 exit:** Engine review re-run → **A** (or A− only if soft formula still debated).

---

## Phase 2 — Simple solid A

### Task 2.1: Risk day plain dates

**Files:**
- Modify: `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs` RiskLine
- Optional: helper next to Coming up date format

- [ ] Format `next_risk_day` as `Fri Mar 6` (local), not raw ISO.
- [ ] Dogfood: risk line matches Coming up style.

---

### Task 2.2: Coming up “Show all in window”

**Why A−:** List top 8; full window totals exist but user can’t act on hidden rows without Full.

**Files:**
- Modify: `HomePage.xaml` Coming up section
- Modify: `HomePage.xaml.cs` ApplyComingUp
- API already has `limit` / full counts

**Steps:**

- [ ] When `fullCount > shown`, show button **Show all (N)** expanding list in-place (no mode flip).
- [ ] Mark paid picker includes all expense rows in window when expanded.
- [ ] Commit `feat: Coming up expand full window on Simple Home`

---

### Task 2.3: Soft until product decision locked

After Task 1.1:

- [ ] If soft still equals STS often → hide remains; label only when soft < STS − $1.
- [ ] If soft is useful under budgets → document in SIMPLE_MODE: “Left after Coming up bills (same cash as Safe).”
- [ ] Remove engineer “IFPP next risk day” from SIMPLE_MODE surface table → “Next risk day”.

---

### Task 2.4: Shared never-neg 409 helper (WinUI)

**Files:**
- Create: `clients/HonestSpend.WinUI/Helpers/NeverNegUi.cs` (or Services)
- Modify: `HomePage.xaml.cs`, `BillsPage.xaml.cs` to call helper

- [ ] One parse + soften + confirm dialog path.
- [ ] Build WinUI green.

**Phase 2 exit:** Simple dogfood feels stupid-simple; re-review → **A**.

---

## Phase 3 — Full solid A

### Task 3.1: Credit progressive disclosure

**Files:**
- Modify: `clients/HonestSpend.WinUI/Pages/CreditPage.xaml` (+ .cs if needed)

**Target information architecture:**

| Tier | Content | Default |
|------|---------|---------|
| **1 — Always open** | Card list · Statement & payment (close/due/policy/timing/funding/fixed) · next payment / honesty one-liner | Expanded |
| **2 — Expand on demand** | Promo lines + calendar · Statement freeze history · Utilization pay-to-target | Collapsed Expander |
| **3 — Advanced / education** | Score factors · payoff compare · rewards pick · educational copy | Collapsed or separate “Learn” expander |

- [ ] Remove any remaining dual Autopay editor chrome (collapsed controls deleted, not just Visibility=Collapsed if dead).
- [ ] One primary CTA: **Save payment settings**.
- [ ] Dogfood: “What do I pay Visa?” answerable without scrolling past score education.

---

### Task 3.2: Plain power copy

- [ ] Payroll package success: `Created payroll day · net $X + tax $Y` — no `#id`.
- [ ] Grep WinUI for `#{` / `schedule #` in user-visible strings; fix.

---

### Task 3.3: Full settings discoverability (light)

- [ ] Never-neg mode (off/warn/hard) labeled in plain English in Settings if missing.
- [ ] `ifpp_cleared_only` / strict pending: “Treat pending as already spent” — no IFPP name.

**Phase 3 exit:** Full re-review → **A** (power complete, not kitchen-sink first paint).

---

## Phase 4 — Grade gate

### Task 4.1: Structured re-review

- [ ] Run dual explore agents: Engine honesty + Simple/Full UX (same rubrics as 2026-08-11 reviews).
- [ ] Required pytest bundle:

```text
.venv\Scripts\python.exe -m pytest ^
  tests/test_schedule_mark_paid.py ^
  tests/test_bank_csv.py tests/test_import_inbox.py ^
  tests/test_autopay_recompute.py tests/test_promo_float_from_lines.py ^
  tests/test_safe_until_cap.py tests/test_safe_until_window.py ^
  tests/test_coming_up.py tests/test_home_simple.py ^
  tests/test_statement_cycle.py tests/test_statement_cycle_api.py ^
  tests/test_wave1_never_neg.py -q --tb=line
```

- [ ] WinUI build Debug x64 clean.
- [ ] Update grades table in this plan + CHANGELOG “trust grades” bullet.

### Task 4.2: A criteria checklist (all must be Yes)

| # | Criterion | Yes/No |
|---|-----------|--------|
| E1 | Soft until base documented + consistent with STS | |
| E2 | One pay-policy authority | |
| E3 | Void/mark-paid card schedule honest | |
| E4 | Digit-safe + match tests green | |
| S1 | No Simple jargon (spot-check Home/Coming up/Settings) | |
| S2 | Soft until useful or intentionally rare with docs | |
| S3 | Coming up expand full window | |
| S4 | Risk day plain date | |
| F1 | Credit progressive disclosure | |
| F2 | No dual pay UI / dead controls | |
| F3 | No raw #ids in Full success copy | |

**Ship solid A only when all Yes + re-review has zero Critical/Important honesty issues.**

---

## Out of scope (do not pull into A grades)

- Mobile / Mac clients  
- Hosted bank feed / paid tiers  
- Full payroll product  
- Day-grid cash runway on Simple Home  
- FICO/Vantage score product  
- Collapsing Simple + Full into one mode  

---

## Effort sketch

| Phase | Effort | Owner type |
|-------|--------|------------|
| 1 Engine | 2–4 days | Engine TDD agents |
| 2 Simple | 1–2 days | WinUI + light engine |
| 3 Full | 2–3 days | WinUI IA |
| 4 Gate | 0.5–1 day | Review agents + human dogfood |

**Total:** ~1–1.5 weeks focused work to solid A across all three, assuming no new product pivots.

---

## Risk register

| Risk | Mitigation |
|------|------------|
| Soft until double-counts bills already in STS | Document formula; test with IFPP + Coming up same bill; prefer post-reserve − window outflows and accept soft ≤ STS |
| Collapsing payment_option breaks discover tests | Keep alias map at wizard edge only |
| Credit expanders hide power users need | Defaults collapsed but remembered session-local if cheap; else one-click “Show advanced” |
| Scope creep to “rewrite Full” | Cap Phase 3 at progressive disclosure + copy, not new features |

---

## Recommended execution order (this session or next)

1. **Phase 1 Task 1.1** (soft until base) — biggest Simple *and* Engine lift.  
2. **Phase 1 Task 1.2** (policy authority) — biggest long-term honesty.  
3. **Phase 2 Tasks 2.1–2.2** (risk date + Coming up expand) — quick Simple A.  
4. **Phase 3 Task 3.1** (Credit expanders) — Full A.  
5. **Phase 4** re-review.

---

## Success one-liner

> **A/A/A means:** one cash math, one pay policy, Simple never lies or jargons, Full is complete **behind** progressive disclosure — not three competing products.
