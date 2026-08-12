# Smart Charge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Can I buy? (and every other pick-a-card surface) recommends the smartest **safe** account — float first, then rewards — and statement promo plans (Amazon/ISB) land in next payment and Safe, without treating the statement as ledger truth.

**Architecture:** One `smart_charge` ranker. Promo terms stay on `promo_installment_lines` (extended) plus `promo_conflicts` for user-vs-statement review. Check is advice; `POST /api/pre-purchase/commit` writes the charge (and purchase-plan line). Import scrape upserts plan tables; D12 forbids silent txn overwrite/delete.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy / pytest; WinUI 3 C#; schema via `migrations.py` (next integer **23**).

## Global Constraints

- Work only on **main**. No feature branches.
- TDD: failing test → implement → pass → commit. Frequent commits.
- Copy: Safe to spend, next payment, next risk day — never IFPP / horizon / runway in UI.
- Ranker order: safety (never-neg, interest-free, next payment still fits Safe) → float days → category rewards. Cash is last among safe options.
- `prefer=auto` is the ranker. `prefer=cash` stays API-only for tests. WinUI has no Prefer box.
- User-sourced promo lines never change silently. Statement vs user → review prompt.
- D12: statement is issuer-cycle truth only. Books own prior transactions. No silent amount/date overwrite or delete.
- Simple: Home · Add · Can I buy? · Sort · 3-minute check. Offer desk is Full books Credit; Simple reaches it from Can I buy? “I have a new offer.”
- Do not change never-neg math, envelope-raid (explicit only), budget-reserve formula, tax vault, Store/Python packaging.
- Clients never re-implement ranker math.

---

## File map

| Path | Responsibility |
|------|----------------|
| `src/honestspend/db.py` | `PromoInstallmentLine` new fields; `PromoConflict`; nullable `account_id` |
| `src/honestspend/migrations.py` | `SCHEMA_VERSION = 23` |
| `src/honestspend/services/promo_installments.py` | Totals only `status=open`; fingerprint; create/update fields |
| `src/honestspend/services/promo_upsert.py` | **New** — upsert + conflict |
| `src/honestspend/services/promo_statement_parse.py` | **New** — extract plan tables / intro from statement text |
| `src/honestspend/services/import_txn_review.py` | **New** — D12 mismatch review items |
| `src/honestspend/services/smart_charge.py` | **New** — ranker |
| `src/honestspend/services/pre_purchase.py` | Check uses ranker; no cash-first auto |
| `src/honestspend/services/pre_purchase_commit.py` | **New** — I charged it |
| `src/honestspend/services/offers.py` | **New** — offer desk decide |
| `src/honestspend/services/rewards_pick.py` | Delegate order to ranker |
| `src/honestspend/services/home_simple.py` | Whisper + promo_brief conflicts |
| `src/honestspend/services/plaid_service.py` | `special` 0% → card_intro upsert |
| `src/honestspend/services/import_bootstrap.py` / `statement_pdf.py` / import apply | Call parse + upsert; D12 |
| `src/honestspend/api/app.py` | commit, offers, conflicts resolve; pre-purchase body |
| `clients/.../Pages/BuyPage.xaml(.cs)` | New form + commit |
| `clients/.../Pages/CreditOffersPage.xaml(.cs)` | **New** offer desk |
| `clients/.../Pages/ImportPage.*` / `CreditPage.*` | Promos found + conflict prompt |
| `clients/.../Services/LedgerApiClient.cs` | New endpoints |
| `docs/SIMPLE_MODE.md`, `docs/BUDGETS.md`, `docs/PRODUCT.md` | Remove cash-first |
| `tests/test_pre_purchase_cash_first.py` | Invert to card-beats-cash |
| `scripts/_dogfood_e2e.py`, `scripts/_northstar_e2e.py` | Expect card rec when float+rewards win |

---

### Task 1: Schema 23 — promo term fields + conflicts

**Files:**
- Modify: `src/honestspend/db.py` (`PromoInstallmentLine`, new `PromoConflict`)
- Modify: `src/honestspend/migrations.py` (`SCHEMA_VERSION` 22 → 23)
- Test: `tests/test_promo_term_schema.py`

**Interfaces:**
- Produces: `PromoInstallmentLine.kind` (`purchase_plan` \| `card_intro` \| `offer`), `offer_type`, `fingerprint`, `linked_txn_id`, `status` (`open` \| `inactive` \| `pending` \| `declined`), `apr`, `account_id` nullable
- Produces: `PromoConflict(id, line_id, incoming_source, field_diffs_json, incoming_values_json, user_values_json, resolved_at)`
- Existing rows: `kind=purchase_plan`, `status=open` if `active` else `inactive`, `source` unchanged

- [ ] **Step 1: Write the failing test**

```python
# tests/test_promo_term_schema.py
def test_schema_23_promo_term_columns(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    init_db(eng)
    assert get_schema_version(eng) == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 23
    cols = {c["name"] for c in inspect(eng).get_columns("promo_installment_lines")}
    for name in ("kind", "offer_type", "fingerprint", "linked_txn_id", "status", "apr"):
        assert name in cols
    assert "promo_conflicts" in inspect(eng).get_table_names()
    # account_id must allow NULL (new_card offer)
    acct_col = next(c for c in inspect(eng).get_columns("promo_installment_lines") if c["name"] == "account_id")
    assert acct_col["nullable"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_promo_term_schema.py::test_schema_23_promo_term_columns -v`

Expected: FAIL (`SCHEMA_VERSION` is 22 or columns missing)

- [ ] **Step 3: Write minimal implementation**

`_mig_23_promo_terms`: recreate `promo_installment_lines` with nullable `account_id` + new columns; `INSERT…SELECT` old rows with `kind='purchase_plan'`, `status` from `active`, `fingerprint` NULL; `CREATE TABLE promo_conflicts`; backfill ORM defaults on `PromoInstallmentLine`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_promo_term_schema.py tests/test_promo_installments.py::test_schema_promo_installment_lines -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/honestspend/db.py src/honestspend/migrations.py tests/test_promo_term_schema.py
git commit -m "feat(schema): promo term fields and promo_conflicts"
```

---

### Task 2: Totals only count open terms + create/update fields

**Files:**
- Modify: `src/honestspend/services/promo_installments.py`
- Test: `tests/test_promo_installments.py` (extend)

**Interfaces:**
```python
def open_promo_totals(session, account_id: int, *, as_of: date) -> tuple[Decimal, Decimal]:
    """Sum principal_remaining, monthly_due for lines with status=='open' and _line_open(...).
    Pending/declined/inactive never enter next payment."""

def create_promo_line(..., kind: str = "purchase_plan", status: str | None = None,
                      offer_type: str | None = None, fingerprint: str | None = None,
                      linked_txn_id: int | None = None, apr: Decimal | None = None,
                      account_id: int | None = None) -> PromoInstallmentLine:
    """status default: 'pending' if kind=='offer' else 'open'."""

def line_to_dict(line) -> dict:  # include kind, status, fingerprint, offer_type, apr, linked_txn_id
```

- [ ] **Step 1: Write the failing test**

```python
def test_pending_offer_excluded_from_totals(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch)
    card = _credit(s)
    create_promo_line(s, card.id, name="Amazon", principal_remaining=Decimal("600"),
                      monthly_payment=Decimal("50"), start_date=date(2026, 8, 1),
                      kind="purchase_plan", status="open")
    create_promo_line(s, card.id, name="Mailer", principal_remaining=Decimal("0"),
                      monthly_payment=Decimal("0"), start_date=date(2026, 8, 1),
                      kind="offer", status="pending")
    prin, monthly = open_promo_totals(s, card.id, as_of=date(2026, 8, 12))
    assert prin == Decimal("600.00")
    assert monthly == Decimal("50.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_promo_installments.py::test_pending_offer_excluded_from_totals -v`

Expected: FAIL (`create_promo_line` unexpected kwargs or pending counted)

- [ ] **Step 3: Write minimal implementation** — filter `status == "open"` (treat NULL status as open for leftover rows). Extend `create_promo_line` / `line_to_dict`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_promo_installments.py tests/test_statement_cycle.py -q`

Expected: PASS

- [ ] **Step 5: Commit** `feat(promo): open totals ignore pending offers`

---

### Task 3: Fingerprint upsert + user-vs-incoming conflict

**Files:**
- Create: `src/honestspend/services/promo_upsert.py`
- Test: `tests/test_promo_upsert.py`

**Interfaces:**
```python
def promo_fingerprint(*, account_id: int | None, kind: str, name: str,
                      monthly: Decimal | None, end_date: date | None) -> str:
    """account_id|kind|norm_name|monthly_or_end.
    purchase_plan uses monthly; card_intro uses end_date; name fallback __intro__ / __isb__."""

def upsert_promo_term(session, *, account_id: int | None, kind: str, name: str,
                      principal_remaining: Decimal, monthly_payment: Decimal,
                      start_date: date, end_date: date | None, source: str,
                      offer_type: str | None = None, apr: Decimal | None = None
                      ) -> dict:
    """Returns {line, created, conflict}.
    If matching fingerprint and existing.source=='user' and remaining/monthly/end/apr differ:
      insert PromoConflict, do not mutate line, conflict=dict.
    If existing.source in (statement, plaid) or fields empty: update line (statement may update plaid;
      plaid must NOT update statement — create conflict instead).
    If no match: create line with that source."""

def resolve_promo_conflict(session, conflict_id: int, *, action: str,
                           edits: dict | None = None) -> PromoInstallmentLine:
    """keep_user | take_incoming | edit. Sets conflict.resolved_at. edit/take_incoming write source=user or incoming_source."""

def list_open_conflicts(session, *, account_id: int | None = None) -> list[dict]: ...
```

- [ ] **Step 1: Failing tests** — (1) statement creates new Amazon line; (2) second scrape updates remaining; (3) user line + different statement remaining → conflict, remaining unchanged; (4) `take_incoming` updates remaining; (5) Plaid cannot silent-update `source=statement`.

- [ ] **Step 2: Run** `pytest tests/test_promo_upsert.py -v` — FAIL (module missing)

- [ ] **Step 3: Implement `promo_upsert.py`**

- [ ] **Step 4: Pass + commit** `feat(promo): upsert terms and user-vs-statement conflicts`

---

### Task 4: Parse statement promo plan tables

**Files:**
- Create: `src/honestspend/services/promo_statement_parse.py`
- Test: `tests/test_promo_statement_parse.py`

**Interfaces:**
```python
def extract_promo_terms(text: str) -> list[dict]:
    """Each dict: kind, name, principal_remaining, monthly_payment, end_date?, months_left?,
    apr?, offer_type?. Never invent rows from a lone '0%' word without a balance or table."""
```

- [ ] **Step 1: Failing fixture test**

Use a realistic Amazon-style blob:

```
Interest Saving Balance
Plan                  Remaining    Monthly payment    Payments left
Amazon Mixmaster      $348.12      $29.01             12
```

Assert one `purchase_plan`, remaining 348.12, monthly 29.01.

Second fixture: `Promotional APR 0% through 03/15/2027  Balance subject to promo $2,100.00` → `card_intro`.

Third: existing `extract_statement_enrichment` sample still works when parse is composed.

- [ ] **Step 2: Run** `pytest tests/test_promo_statement_parse.py -v` — FAIL

- [ ] **Step 3: Implement regex/table parse** (multiline, `$` and commas). Keep conservative — skip rows that fail Decimal.

- [ ] **Step 4: Pass + commit** `feat(import): parse Amazon/ISB and intro promo periods`

---

### Task 5: Import applies promo terms; D12 txn honesty

**Files:**
- Modify: `src/honestspend/services/statement_pdf.py` (after enrich)
- Modify: `src/honestspend/services/import_bootstrap.py` (call extract + upsert)
- Create: `src/honestspend/services/import_txn_review.py`
- Modify: import apply paths that patch existing txns (CSV/OFX/PDF)
- Modify: `src/honestspend/services/import_brief.py` (promos found + conflicts + txn mismatches)
- Test: `tests/test_promo_statement_apply.py`, `tests/test_import_txn_review.py`

**Interfaces:**
```python
def apply_statement_promos(session, account_id: int, text: str) -> dict:
    """extract_promo_terms → upsert_promo_term(source='statement').
    Then recompute_card_payment_schedule. Returns {created, updated, conflicts, lines}."""

def txn_mismatch_reviews(*, existing: Transaction, incoming: dict) -> dict | None:
    """If same fingerprint/external_id and (amount or date) differ: return
    {action: 'review_txn_mismatch', txn_id, books_amount, statement_amount, ...}.
    Caller must NOT write amount/date/payee/category on existing."""
```

- [ ] **Step 1: Failing tests**
  - PDF/text apply on a card: next payment increases by Amazon monthly (policy `statement`).
  - Existing books txn −40 on 2026-08-01; statement same FITID/external_id amount −45 → books stay −40, next_steps include `review_txn_mismatch`.
  - Statement omits a books txn → books row still present.

- [ ] **Step 2: Run tests — FAIL**

- [ ] **Step 3: Wire apply into PDF import + OFX/CSV text enrich; stop any existing-txn field overwrite (D12). Add brief keys `promos_found`, `promo_conflicts`, `txn_mismatches`.**

- [ ] **Step 4: Pass + commit** `feat(import): apply statement promos; never overwrite books txns`

---

### Task 6: `smart_charge` ranker

**Files:**
- Create: `src/honestspend/services/smart_charge.py`
- Test: `tests/test_smart_charge.py`

**Interfaces:**
```python
@dataclass
class ChargeOption:
    method: str                    # cash | card
    account_id: int | None
    account_name: str
    safe: bool
    reason: str
    float_days: int
    rewards_rate: Decimal          # percent points
    next_payment_after: Decimal | None
    promo_used: str | None         # none | card_intro | purchase_plan
    remaining_spendable: Decimal | None

def rank_charge(session, *, amount: Decimal, reward_category: str,
                proposed_account_id: int, as_of: date | None = None,
                profile_id: int | None = None, scope: str | None = None,
                promo: dict | None = None, budget_category_id: int | None = None,
                allow_envelope_raid: bool = False) -> dict:
    """promo = {mode: none|card_intro|purchase_plan, months?: int, monthly?: str}
    Returns {proposed, recommended, options, verdict, why, budget_check, envelope_raid, safe_to_spend}."""
```

**Safety (unsafe):**
- Cash: `min(acct_avail, effective_safe) < amount` (effective_safe = Safe, or cash_raw if raid).
- Card with no `payment_due_day` (and no close+timing): unsafe, reason “Add due day and pay-from on this card.”
- Card: IFPP `safe_to_charge` < amount **or** after hypothetical books+amount (and optional new monthly) `project_card_payment` next_payment > Safe to spend.
- Purchase-plan mode: monthly = `monthly` or `amount/months`; include that monthly in the hypothetical next payment.

**Rank safe options:** `(-float_days, -rewards_rate, proposed_first, util, next_payment_increase)`.

Cash `float_days=0`, `rewards_rate=0`.

Card float_days = `(next_payment_date - as_of).days` (0 if missing date — already unsafe).

- [ ] **Step 1: Failing tests** (exact fixtures)

```python
def test_safe_rewards_card_beats_cash():
    # checking 5000 Safe, card due in 25d, 5% groceries, 0% promo path, amount 100
    rec = rank_charge(...)["recommended"]
    assert rec["method"] == "card"
    assert rec["account_name"] == "Rewards"

def test_card_that_squeezes_next_payment_loses_to_cash():
    # Safe 120, card next payment already 100, amount 80 → card unsafe, cash wins if cash ok

def test_more_float_beats_higher_rewards():
    # card A due in 40d 1% gas; card B due in 10d 5% gas → A wins

def test_missing_due_day_unsafe():
    # card no due day → proposed.safe is False, reason mentions due day
```

- [ ] **Step 2: Run** `pytest tests/test_smart_charge.py -v` — FAIL

- [ ] **Step 3: Implement ranker** using `run_ifpp`, `budget_reserve_total`, `rate_for_category`, `project_card_payment`. Do **not** mutate books; simulate next payment in memory (copy amounts) — if `project_card_payment` always reads DB, add `project_card_payment(..., extra_balance=Decimal, extra_monthly=Decimal)` optional kwargs rather than committing.

- [ ] **Step 4: Pass + commit** `feat: smart_charge ranker (float then rewards)`

---

### Task 7: Pre-purchase check uses ranker (kill cash-first)

**Files:**
- Modify: `src/honestspend/services/pre_purchase.py`
- Modify: `src/honestspend/api/app.py` (`PrePurchaseIn`)
- Modify: `tests/test_pre_purchase_cash_first.py` (invert)
- Modify: `docs/BUDGETS.md`, `docs/SIMPLE_MODE.md` (remove “auto prefers cash”)
- Test: keep envelope/budget tests; they pass `prefer="cash"` explicitly

**Interfaces:**
```python
class PrePurchaseIn(BaseModel):
    amount: Decimal
    prefer: str = "auto"  # auto=ranker | cash | card
    proposed_account_id: int | None = None
    account_id: int | None = None          # legacy alias → proposed_account_id
    reward_category: str = "general"
    promo: dict | None = None
    category_id: int | None = None         # budget only
    ...

def check_purchase(...) -> dict:
    """prefer==auto → rank_charge (proposed_account_id required in WinUI; if omitted, pick IFPP primary cash as proposed).
    prefer==cash → old cash-only rec (tests).
    principles[] must NOT contain 'Auto prefers cash'."""
```

- [ ] **Step 1: Change `test_auto_prefers_cash_when_both_safe` to `test_auto_prefers_safe_card_when_float_and_rewards`** asserting `recommended.method == "card"`. Run — FAIL (still cash).

- [ ] **Step 2: Implement `check_purchase` via `rank_charge` for auto.** Keep envelope_raid / budget_check wrapping.

- [ ] **Step 3: Run** `pytest tests/test_pre_purchase_cash_first.py tests/test_pre_purchase_intermix.py tests/test_pre_purchase_envelope_raid.py tests/test_setup_budgets_buffers.py -q`

Expected: PASS

- [ ] **Step 4: Commit** `feat: Can I buy? auto uses smart_charge not cash-first`

---

### Task 8: I charged it — commit endpoint

**Files:**
- Create: `src/honestspend/services/pre_purchase_commit.py`
- Modify: `src/honestspend/api/app.py`
- Test: `tests/test_pre_purchase_commit.py`

**Interfaces:**
```python
class PrePurchaseCommitIn(BaseModel):
    amount: Decimal
    post_account_id: int
    proposed_account_id: int
    recommended_account_id: int
    reward_category: str = "general"
    promo: dict | None = None
    category_id: int | None = None
    payee: str | None = None
    confirm_unsafe: bool = False
    commit_key: str | None = None
    profile_id: int | None = None

def commit_purchase(session, **body) -> dict:
    """post_account_id must be recommended or proposed.
    Re-run rank_charge; if post is unsafe and not confirm_unsafe → raise UnsafePurchase (409).
    Write Transaction (−amount). If promo.mode==purchase_plan: create_promo_line kind=purchase_plan
    source=user linked_txn_id=txn.id monthly=months split or given monthly.
    recompute_card_payment_schedule if credit.
    Idempotent: if commit_key seen on memo/external_id `commit:{key}`, return existing.
    Returns {transaction, promo_line, recommended, posted}."""
```

- [ ] **Step 1: Failing tests** — commit recommended creates card charge; commit proposed cash creates cash txn; purchase_plan commit creates line and next payment += monthly; unsafe without confirm raises; double commit_key does not double-post.

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement + `POST /api/pre-purchase/commit`** mapping ValueError/UnsafePurchase → 400/409.

- [ ] **Step 4: Pass + commit** `feat: pre-purchase commit writes charge and purchase plan`

---

### Task 9: Buy page form + commit actions

**Files:**
- Modify: `clients/HonestSpend.WinUI/Pages/BuyPage.xaml`
- Modify: `clients/HonestSpend.WinUI/Pages/BuyPage.xaml.cs`
- Modify: `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- Modify: `clients/HonestSpend.WinUI/MainWindow.xaml.cs` (nav to offers if needed)

**Interfaces (C#):**
```csharp
Task<JsonElement> PrePurchaseAsync(decimal amount, int proposedAccountId,
    string rewardCategory, object? promo, int? budgetCategoryId, bool allowEnvelopeRaid, ...);
Task<JsonElement> CommitPurchaseAsync(object body, ...);
Task<JsonElement> GetAccountsAsync(...); // already exists
```

- [ ] **Step 1: Replace Prefer box with Proposed account ComboBox, Reward category ComboBox (canonical keys), optional Promo (None / Card intro / Purchase plan months+monthly / New offer), keep optional budget category. Subtitle: no cash-first.**

- [ ] **Step 2: Check calls new PrePurchase body. Headline = recommended. If recommended id ≠ proposed, show both reasons.**

- [ ] **Step 3: After a successful check, show `I charged the recommended account`. If they differ, also `I charged my original pick`. Both call commit with the matching `post_account_id`. 409 → confirm dialog then `confirm_unsafe=true`.**

- [ ] **Step 4: Promo “I have a new offer” navigates to Credit Offers (page can be a stub until Task 12 — navigate to Credit with tag `offers` or temporary InfoBar “Opens Offers in Full books”).**

- [ ] **Step 5: Build WinUI; commit** `feat(ui): Can I buy? smart form and I charged it`

---

### Task 10: Rewards pick + Home whisper use ranker

**Files:**
- Modify: `src/honestspend/services/rewards_pick.py`
- Modify: `src/honestspend/services/home_simple.py` (float whisper / promo_brief)
- Modify: `clients/.../Pages/CreditPage.xaml.cs` if Best card calls pick
- Test: `tests/test_rewards_pick.py` (order = float then rate), `tests/test_home_simple.py` if present

**Interfaces:**
```python
def pick_card_for_category(...) -> dict:
    """Build a dummy amount (amount or 1.00), call rank_charge with proposed=first card.
    Return cards in ranker order. Hint text: rewards never beat a tighter pay date."""
```

Home whisper: use ranker’s top safe card, not “best rewards only.”

`promo_brief`: include `open_conflict_count` and promo ending within 45 days.

- [ ] **Step 1: Failing test** — two cards, higher rate but sooner due loses in `pick_card_for_category`.

- [ ] **Step 2: Implement + pass**

- [ ] **Step 3: Commit** `feat: best-card and Home whisper use smart_charge`

---

### Task 11: Offer desk service + APIs

**Files:**
- Create: `src/honestspend/services/offers.py`
- Modify: `src/honestspend/api/app.py`
- Test: `tests/test_offers.py`

**Interfaces:**
```python
def create_offer(session, *, offer_type: str, **terms) -> PromoInstallmentLine:
    """kind=offer, status=pending. new_card may have account_id=None."""

def decide_offer(session, offer_id: int, *, decision: str, confirm_skip: bool = False) -> dict:
    """take | take_with_plan | skip.
    Verdict from rank_charge + fee vs estimated interest.
    skip without confirm_skip if verdict is Take → still allowed.
    take new_card: create credit Account + card_intro open line; offer status=inactive.
    take BT: fee txn + transfer/principal move + intro on destination.
    take purchase_plan: create open purchase_plan on account.
    take_with_plan: same but set autopay_policy to statement or promo_sink when books would kill 0%."""

def offer_verdict(session, offer: PromoInstallmentLine) -> dict:
    """take | take_with_plan | skip + why."""
```

API: `GET/POST /api/offers`, `POST /api/offers/{id}/decide`, `GET /api/promos/conflicts`, `POST /api/promos/conflicts/{id}/resolve`.

- [ ] **Step 1: Failing tests** — BT fee > interest saved → skip; opposite + Safe fits → take; new_card accept creates account; skip sets declined.

- [ ] **Step 2: Implement + pass + commit** `feat: offer desk decide (new card, BT, plan)`

---

### Task 12: Credit Offers WinUI + import conflict prompt

**Files:**
- Create: `clients/HonestSpend.WinUI/Pages/CreditOffersPage.xaml(.cs)`
- Modify: `MainWindow.xaml` Full books nav item **Offers & promos**
- Modify: `ImportPage.xaml(.cs)` — after import, show Promos found + ContentDialog for each conflict (Keep mine / Take statement / Edit)
- Modify: `BuyPage` new-offer nav to this page
- Modify: `LedgerApiClient.cs`

- [ ] **Step 1: Offers page** — list pending offers; form for three types; verdict text; Take / Take with a plan / Skip.

- [ ] **Step 2: Manual editor** — existing card promo lines (reuse GET/POST/PATCH promo-lines). Show source.

- [ ] **Step 3: Import dialog** binds `promo_conflicts`; resolve API.

- [ ] **Step 4: Commit** `feat(ui): offer desk and statement promo review`

---

### Task 13: Plaid promo upsert

**Files:**
- Modify: `src/honestspend/services/plaid_service.py`
- Test: `tests/test_plaid_promo_upsert.py` (mock liabilities payload)

**Interfaces:**
```python
def apply_plaid_promo_aprs(session, account_id: int, aprs: list[dict]) -> dict:
    """If any apr_type in (special,) or apr_percentage==0 with balance_subject_to_apr>0:
    upsert_promo_term kind=card_intro source=plaid. Never create purchase_plan rows."""
```

Call after existing liabilities sync.

- [ ] **Step 1: Failing test** — special 0% / 1000 balance → card_intro; no purchase_plan; user line conflict not overwritten.

- [ ] **Step 2: Implement + pass + commit** `feat(plaid): upsert card intro from special APR`

---

### Task 14: Product-wide audit + e2e

**Files:**
- `docs/PRODUCT.md` — spendable/IFPP row: Can I buy? uses smart_charge
- `docs/SIMPLE_MODE.md` — Can I buy? description
- `docs/BUDGETS.md` — remove “auto prefers cash”
- `CHANGELOG.md` Unreleased
- `scripts/_dogfood_e2e.py` / `scripts/_northstar_e2e.py` — fixture with rewards card + due day; assert recommended method card
- `src/honestspend/services/ifpp_simulate.py` — optional `account_id` + promo for what-if (if tests exist)
- Grep: `prefers cash`, `PreferBox`, `Auto prefers cash`

**Done-when checks (must all be true):**

1. `prefer=auto` never returns cash solely because Safe covers while a safe card exists.
2. Statement Amazon-style plan changes next payment without a manual line.
3. User vs statement promo conflict blocks apply until review.
4. Docs/Buy copy do not say cash-first.
5. Dogfood/northstar assert card rec in the rewards+float fixture.
6. D12: mismatched statement amount does not change books txn.

- [ ] **Step 1: Grep + fix leftovers**

Run: `pytest tests/test_smart_charge.py tests/test_pre_purchase_cash_first.py tests/test_promo_upsert.py tests/test_promo_statement_parse.py tests/test_pre_purchase_commit.py tests/test_offers.py -q`

- [ ] **Step 2: Run** `python scripts/_dogfood_e2e.py` (or project’s usual invoke) — card rec assertion green.

- [ ] **Step 3: Commit** `docs: smart-charge audit — no cash-first; changelog`

---

## Spec coverage

| Spec | Task |
|------|------|
| D1–D2 ranker | 6, 7 |
| D3 promo kinds | 1–2, 11 |
| D4 one ranker everywhere | 7, 10, 14 |
| D5–D6 check / commit / post choice | 8, 9 |
| D7 form fields | 9 |
| D8 offer desk | 11, 12 |
| D9 statement scrape including Amazon | 4, 5 |
| D10 conflict review | 3, 5, 12 |
| D11 audit | 10, 14 |
| D12 books vs statement txns | 5, 14 |
| Plaid best-effort | 13 |
| PR plan 1–5 | Tasks 1–5 / 6–7 / 8–9 / 11–12 / 13–14 |

## Self-review notes

- No TBD. `project_card_payment` extra kwargs are defined in Task 6 so Task 8 can reuse them.
- `prefer=cash` remains for existing budget/envelope tests — not a product default.
- CreditOffersPage may be registered before Task 12 if Task 9 navigates there; Task 9 allows Credit tag stub so Task 9 can ship without the full desk.
