# Coming Up Strip (Calendar Days + Paydays) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simple Home shows a plain **Coming up** list of cash outflows (and optional inflows) from now until either **N calendar days** or **the next 1–2 paydays**, so paycheck-to-paycheck users see “until Friday” without opening Full books.

**Architecture:** New pure service `coming_up.py` resolves a projection window (calendar or payday-based), expands **cash-side** schedules only (same skip rule as IFPP: no credit-account bills), and returns a ranked short list. `home_simple` embeds it; WinUI Simple Home renders the list. Payday = next occurrence(s) of active **income** schedules (`kind=income` or `amount > 0`).

**Tech Stack:** Python FastAPI/SQLAlchemy, pytest, WinUI 3 C#

## Global Constraints

- Everyday / Simple first: plain language only — never IFPP, never engine jargon.
- Cash honesty: expand only schedules that hit **cash** Safe path (skip credit-account schedules; include `Card payment · …` on checking).
- Local-first freeware; entity scope default (This money / Who), optional group.
- TDD; no silent empty window when income exists but is mis-tagged if we can recover via amount sign.
- YAGNI: no full runway chart in Simple (that stays `cash_runway` / Full books); this is a **short list**, not a day grid.
- Defaults: window mode prefers **paydays when income is known**, else **14 calendar days**.

---

## Product rules (locked)

### Window modes

| Mode | Meaning | End date |
|------|---------|----------|
| `calendar` | Fixed horizon | `as_of + calendar_days` (allowed **7–14**, default **14**) |
| `paydays` | Through next N paychecks | Date of the **Nth** future income occurrence (N = **1 or 2**, default **1**) |
| `auto` (default) | Smart pick | If ≥1 active income schedule in scope → `paydays` with N=1; else `calendar` with 14 days |

**Cap:** Even in payday mode, never project past `as_of + 45` days (safety). If the Nth payday is beyond 45d, end at 45d and set `window_capped: true`.

**Minimum:** If payday is today or tomorrow, still include events through that day; if **no** income and mode=`paydays`, **fall back to calendar 14** and set `window_fallback: "no_income"`.

### What appears in the list

**Include (outflows):**
- Active scheduled items expanded into `[as_of, window_end]` with **amount &lt; 0**
- Not on a credit account (`_is_credit_account_schedule` → skip)
- Certainty: always include `fixed`; include `expected` in expected/optimistic modes; include `historical_avg` only if mode is not `conservative` (match IFPP weighting spirit — for **listing**, show name/amount if weight &gt; 0 for income; for **expenses always show**)

**Include (inflows) — optional, default on for payday mode:**
- Income occurrences in the window (especially the payday boundary row)
- Mark `direction: "in"` so UI can style differently

**Sort:** by `on_date` ascending, then larger absolute amount first.

**Limit:** top **8** rows for Simple (enough for 1–2 pay periods); full untruncated available via API query `limit=50`.

### Copy (Simple)

| Field | Example |
|-------|---------|
| Section title | **Coming up** |
| Subtitle | `Until Fri Sep 12 (next payday)` or `Next 14 days` or `Until 2nd payday · Fri Sep 26` |
| Row | `Fri · Card payment · Visa · $340` / `Mon · Rent · $1,600` / `Fri · Paycheck · +$2,100` |
| Empty | `Nothing scheduled in this window — add a bill or paycheck in Add` |

Never say “horizon”, “expand_scheduled”, “IFPP”.

### Settings (optional v1.1 — Task 4 can be light)

| Setting | Values | Default |
|---------|--------|---------|
| `coming_up_window_mode` | `auto` \| `calendar` \| `paydays` | `auto` |
| `coming_up_calendar_days` | 7–14 | 14 |
| `coming_up_payday_count` | 1–2 | 1 |
| `coming_up_show_income` | bool | true |

Store on `AppSettings` (migration next integer). If Task 4 is deferred, hardcode defaults in service and skip settings UI.

---

## File map

| Path | Responsibility |
|------|----------------|
| `src/financial_os/services/coming_up.py` | **New** — window resolve + list builder |
| `src/financial_os/services/home_simple.py` | Embed `coming_up` in Simple payload |
| `src/financial_os/api/app.py` | `GET /api/coming-up` (+ optional settings fields) |
| `src/financial_os/db.py` / `migrations.py` | Optional AppSettings columns (Task 4) |
| `clients/.../LedgerApiClient.cs` | `GetComingUpAsync` |
| `clients/.../Pages/HomePage.xaml(.cs)` | Coming up card under Safe / risk line |
| `docs/SIMPLE_MODE.md` | Document strip |
| `tests/test_coming_up.py` | Window + list + IFPP skip rules |

---

### Task 1: Window resolution + coming_up service (TDD)

**Files:**
- Create: `src/financial_os/services/coming_up.py`
- Test: `tests/test_coming_up.py`

**Interfaces:**

```python
# coming_up.py

WindowMode = Literal["auto", "calendar", "paydays"]

def is_income_schedule(item: ScheduledItem) -> bool:
    """True if kind==income or amount > 0."""
    ...

def next_income_dates(
    session: Session,
    *,
    as_of: date,
    profile_id: int | None,
    scope: str,  # entity|group
    count: int = 2,
    max_days: int = 45,
) -> list[date]:
    """Next N income occurrence dates from active income schedules (cash or unassigned OK)."""
    ...

def resolve_window(
    session: Session,
    *,
    as_of: date,
    mode: WindowMode = "auto",
    calendar_days: int = 14,
    payday_count: int = 1,
    profile_id: int | None = None,
    scope: str | None = None,
) -> dict:
    """
    Returns:
      mode_effective: calendar|paydays
      window_start: as_of
      window_end: date
      calendar_days: int | null
      payday_count: int | null
      paydays: list[str ISO]
      label: str  # plain English for subtitle
      window_fallback: str | null  # e.g. no_income
      window_capped: bool
    """
    ...

def build_coming_up(
    session: Session,
    *,
    as_of: date | None = None,
    mode: WindowMode = "auto",
    calendar_days: int = 14,
    payday_count: int = 1,
    profile_id: int | None = None,
    scope: str | None = None,
    ifpp_mode: str | None = None,
    limit: int = 8,
    show_income: bool = True,
) -> dict:
    """
    Returns:
      window: resolve_window(...)
      items: [
        {
          on_date: ISO,
          weekday: "Fri",  # locale en short ok
          name: str,
          amount: str,  # signed decimal string
          direction: "out"|"in",
          account_id: int | null,
          account_name: str | null,
          scheduled_id: int,
          kind: expense|income,
          is_card_payment: bool,  # name startswith "Card payment"
        },
        ...
      ]
      outflow_total: str
      inflow_total: str
      count: int
      empty_hint: str | null
    """
    ...
```

**Implementation notes:**
- Reuse `_resolve_scope_and_profile`, `_active_accounts`, `_is_credit_account_schedule` from `ifpp_service`.
- Expand with `expand_scheduled` for each candidate item; horizon_end = window_end.
- Income detection for paydays: query active `ScheduledItem` where `(kind == "income") OR (amount > 0)`, expand occurrences ≥ as_of, sort unique dates, take first N within max_days.
- Calendar days clamp: `calendar_days = max(7, min(14, calendar_days))`.
- Payday count clamp: `max(1, min(2, payday_count))`.
- Label examples:
  - paydays N=1: `Until {weekday} {Mon} {D} (next payday)`
  - paydays N=2: `Until {weekday} {Mon} {D} (2nd payday)`
  - calendar: `Next {n} days`
  - fallback: `Next 14 days (no paycheck scheduled)`

- [ ] **Step 1: Failing tests**

```python
def test_resolve_window_calendar_14():
    # no income → auto uses calendar 14
    ...

def test_resolve_window_next_payday():
    # biweekly income next_date = as_of+5 → window_end that date, mode_effective paydays

def test_resolve_window_two_paydays():
    # biweekly → 2nd occurrence ~14 days later

def test_coming_up_includes_card_payment_on_cash_skips_credit_netflix():
    # Card payment · Visa on checking included; Netflix on credit skipped

def test_coming_up_sorts_by_date():
    ...
```

- [ ] **Step 2: Run** `pytest tests/test_coming_up.py -v` — expect FAIL (import/module)

- [ ] **Step 3: Implement `coming_up.py`**

- [ ] **Step 4: Run** — all pass

- [ ] **Step 5: Commit**

```bash
git add src/financial_os/services/coming_up.py tests/test_coming_up.py
git commit -m "feat: coming-up window (calendar + 1–2 paydays)"
```

---

### Task 2: API + home_simple embed

**Files:**
- Modify: `src/financial_os/services/home_simple.py`
- Modify: `src/financial_os/api/app.py`
- Test: `tests/test_coming_up.py` (API) and/or `tests/test_home_simple.py`

**Interfaces:**
- `GET /api/coming-up?mode=auto&calendar_days=14&payday_count=1&profile_id=&scope=&limit=8`
- `build_home_simple` return key:

```python
"coming_up": build_coming_up(session, as_of=as_of, profile_id=pid, scope=sc, ...)
```

Use same profile/scope resolution as Home (entity default).

- [ ] **Step 1: Test** home payload contains `coming_up.items` list; GET `/api/coming-up` 200

- [ ] **Step 2: Implement routes + home embed**

```python
@app.get("/api/coming-up")
def get_coming_up(
    mode: str = "auto",
    calendar_days: int = 14,
    payday_count: int = 1,
    limit: int = 8,
    show_income: bool = True,
    profile_id: Optional[int] = None,
    scope: Optional[str] = None,
    as_of: Optional[date] = None,
    db: Session = Depends(get_db),
):
    from financial_os.services.coming_up import build_coming_up
    return build_coming_up(
        db,
        as_of=as_of,
        mode=mode if mode in ("auto", "calendar", "paydays") else "auto",
        calendar_days=calendar_days,
        payday_count=payday_count,
        profile_id=profile_id,
        scope=scope,
        limit=limit,
        show_income=show_income,
    )
```

- [ ] **Step 3: Pass tests + commit** `feat(api): coming-up on home and /api/coming-up`

---

### Task 3: WinUI Simple Home — Coming up card

**Files:**
- Modify: `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- Modify: `clients/HonestSpend.WinUI/Pages/HomePage.xaml`
- Modify: `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`

**UI placement:** New `Border` **below** Safe/risk card, **above** Budgets (Simple panel only).

```xml
<Border x:Name="ComingUpCard" ...>
  <StackPanel>
    <TextBlock Text="Coming up" FontWeight="SemiBold" />
    <TextBlock x:Name="ComingUpSubtitle" Opacity="0.7" />
    <ItemsControl x:Name="ComingUpList" />
    <TextBlock x:Name="ComingUpEmpty" Opacity="0.65" Visibility="Collapsed" />
    <TextBlock x:Name="ComingUpTotals" Opacity="0.7" FontSize="12" />
  </StackPanel>
</Border>
```

**Row format (code-behind):**  
`{weekday} · {name} · {money}`  
Outflows as currency absolute or signed per existing `JsonUi.Money`; inflows with `+`.

**Data:** Prefer `coming_up` from `GetHomeSimpleAsync()`; no extra round-trip required. Optional later: settings toggles.

- [ ] **Step 1: Wire `ApplyComingUp(_home)` after Safe load**

- [ ] **Step 2: Build Release x64**

- [ ] **Step 3: Commit** `feat(winui): Coming up list on Simple Home`

---

### Task 4: Settings (optional but recommended)

**Files:**
- Modify: `db.py` AppSettings + `migrations.py` (SCHEMA_VERSION += 1)
- Modify: Settings page or keep API-only for v1
- Test: migration + resolve reads settings when mode not passed

Columns:
- `coming_up_window_mode` VARCHAR default `auto`
- `coming_up_calendar_days` INT default 14
- `coming_up_payday_count` INT default 1
- `coming_up_show_income` BOOL default 1

`build_coming_up` / home_simple: if caller does not pass overrides, read AppSettings.

WinUI Settings (Full books Settings is fine):  
- Window: Auto / Next 7–14 days / Next payday / Next 2 paydays  
- Map to mode + calendar_days + payday_count

- [ ] **Step 1: Migration + defaults tests**

- [ ] **Step 2: Wire read path**

- [ ] **Step 3: Minimal Settings UI or API-only + docs**

- [ ] **Step 4: Commit** `feat: coming-up window settings`

If time-boxed: **skip WinUI settings**, ship defaults only; document API query params.

---

### Task 5: Docs + empty-state copy

**Files:**
- Modify: `docs/SIMPLE_MODE.md`
- Modify: `docs/STATEMENT_CYCLES.md` (one line: card payments appear in Coming up)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document window modes + cash-side rule**

- [ ] **Step 2: Commit** `docs: Coming up strip (days + paydays)`

---

## Acceptance scenarios

| # | Setup | Expect |
|---|--------|--------|
| A | No income, rent Fri, Netflix on credit, Card payment Visa on checking Tue | Auto → 14 days; list has Rent + Card payment; **no** Netflix |
| B | Biweekly paycheck next Friday, bills Mon/Wed | Auto → paydays 1; subtitle “next payday”; window ends Friday; Mon/Wed bills listed |
| C | Same, `payday_count=2` | Window ends 2nd paycheck (~14d later); more bills included |
| D | `mode=calendar&calendar_days=7` | Hard 7-day end regardless of income |
| E | Empty schedules | Empty hint; no crash |
| F | Income only, no bills | Optional income rows; outflow_total 0 |

---

## Out of scope

- Editing/deleting schedules from the list (v2)
- Mark bill paid from the row (v2 — “Pay this card” suggestion #4)
- Full day-grid runway in Simple (exists as API `cash-runway` for power)
- Predicting payday from transaction history without a scheduled income (v2)

---

## Self-review checklist

- [x] Spec coverage: calendar 7–14, paydays 1–2, auto, cap 45d, Simple UI, home embed  
- [x] No TBD placeholders  
- [x] Interfaces consistent across tasks (`build_coming_up` shape)  
- [x] Cash-side skip rule explicit  

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-11-coming-up-paydays.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  

**2. Inline Execution** — this session, executing-plans with checkpoints  

**Which approach?**
