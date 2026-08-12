# Smart charge — Can I buy? + promo terms + offer desk

**Status:** Draft — awaiting user review  
**Date:** 2026-08-12  
**Product:** Everyone (smart fiscal decisions, not cash-first)

## Intent

HonestSpend is a smart-money product. **Can I buy?** must recommend **which account** to charge from rewards, statement close, pay dates, and promo terms — never “use cash because Safe covers it.”

The same opinion must hold everywhere: Home, Credit best-card, import, tray/Glance, what-if.

Promo periods on statements (Amazon monthly / ISB plans, retail purchase 0%, listed intro balances) must land in next payment and Safe to spend. If a statement disagrees with a line you typed, the user decides — we never silently overwrite.

## Locked decisions

| # | Decision | Why |
|---|---|---|
| D1 | Always lead with the smartest **safe** account. Proposed pick stays visible. | Engine has an opinion every time. |
| D2 | Among safe: **float first** (longest interest-free path that does not squeeze the next pay date), then **category rewards**. Checking wins only when no card is safe, or charging would make the next card payment unsafe. | Fiscal soundness #1; 0% float is first-class; rewards never beat a tighter pay date. |
| D3 | Three promo kinds: **purchase plan** (Amazon / retail monthly + ISB), **card intro** (existing 0% window), **offer** (new card / BT / plan not yet on books). | Real issuer products. |
| D4 | One ranker (`smart_charge`). Can I buy?, offer desk, Home whisper, Credit best-card, import attach all call it. | No three brains. |
| D5 | Can I buy? is two-step: **Check** = advice; **I charged it** writes books. | Don’t invent spend. |
| D6 | At post time: **I charged the recommended account** and **I charged my original pick** (when they differ). Unsafe original pick needs never-neg / APR confirm. | User can override. |
| D7 | Form: amount, proposed account, **reward category (required)**, optional promo, optional **budget category** (warning only). Drop Prefer cash/card/auto. | Rewards pick the card; budget does not. |
| D8 | Offer desk lives at **Full books → Credit → Offers & promos**. Simple reaches it from Can I buy? promo = “I have a new offer.” | No extra Simple tab. |
| D9 | Statement scrape is a **required** promo source, including Amazon-style plan tables. Plaid is best-effort (APR buckets, due dates — usually no plan rows). | Plaid will miss retail monthlies. |
| D10 | **User-sourced lines never change silently.** Statement or Plaid vs a `source=user` line → **review prompt** (keep yours / take statement / edit). Silent upsert only for empty fields or lines whose source is `statement` / `plaid`. Among silent sources: **statement > Plaid**. | Honesty + user control. |
| D11 | Product-wide audit is **in this plan**, not a later maybe. Known cash-first / rewards-only call sites must switch to the ranker. | The whole app’s job is smart fiscal decisions. |

## Out of scope

- Applying for a card at the issuer, bureau pulls, loan/card marketplace ads.
- Computing a FICO/Vantage score.
- Payroll, e-file, brokerage.
- Native Mac/Linux/mobile clients.
- One-tap “post on Check” (rejected: D5).
- A second promo schema beside `promo_installment_lines` + account intro fields.
- Changing never-neg, envelope raid (still explicit), budget-reserve math, tax vault, Store/Python packaging.

## Current product (what we change)

Today `POST /api/pre-purchase` with `prefer=auto` picks cash when Safe covers (`pre_purchase.py`, `test_pre_purchase_cash_first.py`, Buy page Prefer box). Card rewards are a fuzzy program-name score, not `rewards_rates_json`. Close/due exist on cards but do not rank Can I buy?.

We already have:

- `promo_installment_lines` (ISB: principal, monthly, start/end, `source` user\|import\|statement\|isb).
- Account `promo_apr`, `promo_end_date`, `promo_balance`.
- Statement cycle + cash `Card payment · {nickname}`.
- `GET /api/rewards/pick` (rate + room only).
- PDF/OFX bootstrap: card-level APR/intro/end if empty — **not** plan tables.

## Model

### Promo term (one shape)

Extend `promo_installment_lines`. Pending offers use the same table with `status=pending` and **do not** enter statement/next-payment math until accept. Do not add a parallel promo family. Conflicts live in a small `promo_conflicts` table (not a line status), so a user line stays `open` until they resolve.

| Field | Role |
|---|---|
| `kind` | `purchase_plan` \| `card_intro` \| `offer` |
| `offer_type` | null \| `new_card` \| `balance_transfer` \| `purchase_plan` (offers only) |
| `account_id` | Required except `kind=offer` + `offer_type=new_card` (no card yet) |
| `name` | “Amazon plan · Mixmaster”, “Intro 0% purchases” |
| `principal_remaining` | ISB carve-out (0 for pending offers) |
| `monthly_payment` | Added to next payment **only when** `status=open` |
| `apr` | Usually 0 for these paths |
| `start_date` / `end_date` | Window |
| `source` | `user` \| `statement` \| `plaid` \| `import` |
| `fingerprint` | Stable match key for upsert / conflict |
| `linked_txn_id` | Optional charge that created a purchase plan |
| `status` | `open` (in math) \| `inactive` \| `pending` (offer not taken) \| `declined` |

**Card-level intro** may stay on `Account.promo_apr` / `promo_end_date` / `promo_balance` **and** be mirrored as one `kind=card_intro` line so the ranker and scrape have one list. Writer of the account fields also upserts the line (and the reverse when the line is the source of truth).

**Fingerprint** = `account_id` + `kind` + normalized name (or `"__intro__"` / `"__isb__"`) + monthly amount (purchase plans) or end date (intro). Same fingerprint → update remaining / monthly / end, not a duplicate.

**Cash impact** (unchanged math, more complete inputs):

```
statement balance = max(0, books − sum(open purchase_plan + card_intro principal))
next payment     = f(pay policy, statement, min, fixed) + sum(open purchase_plan monthlies)
```

Amazon/retail monthlies **must** appear in `sum(monthlies)` once scraped or posted via **I charged it**.

### `smart_charge` ranker

Pure service. Clients never re-rank.

**Inputs:** `amount`, `as_of`, `profile_id` / `scope`, `reward_category` (required), `proposed_account_id`, optional `promo` (`none` \| `card_intro` \| `purchase_plan` with months and/or monthly \| `new_offer`), optional `budget_category_id`, `allow_envelope_raid`.

**Candidate set:** cash accounts and credit accounts in scope, plus the proposed account even if out of scope (still must pass safety). Offers that are not accepted are **not** charge candidates (they belong on the offer desk).

**Safety gate (drop or mark unsafe):**

1. Cash: available after account buffer, capped by Safe to spend (or cash-before-budgets if envelope raid allowed) ≥ amount. Never-neg on checking.
2. Card: interest-free capacity ≥ amount **and** after this charge the **next cash card payment still fits Safe** (recompute cycle with the new books + any new purchase-plan monthly). Missing close/due → treat as **no proven float**; card is unsafe until due day (and pay-from) exist, with reason “Add due day and pay-from on this card.”
3. Purchase-plan path: monthly = `amount / months` (or user monthly); next payment includes that monthly; same Safe test.
4. Card intro path: charge is interest-free through `end_date` only if planned payoff (policy + Safe on those dates) does not revolve after the window.

**Rank among remaining safe:**

1. **Float days** = calendar days until cash actually leaves for this charge (cash = 0; card = days until that card’s next payment date under its timing). A **purchase plan on the same card** has the **same** float days as a regular charge; it wins in the **safety** step when the full statement would squeeze Safe but the monthly would not.
2. If float days tie: **higher reward rate** for `reward_category` (`rewards_rates_json`, else program heuristic, else 0). Cash rate = 0.
3. If still tied: proposed account, then lower utilization, then smaller increase in next payment.

Checking is just another candidate (0 float, 0 rewards). It wins only if no card is safe.

**Outputs:**

- `proposed`: grade (safe, reason, float_days, rewards_rate, next_payment_after, promo_used).
- `recommended`: winning option (may equal proposed).
- `options[]`: all candidates, safe first.
- `verdict`: `safe` \| `safe_via_other_account` \| `safe_budget_tight` \| `safe_raid_envelope` \| `do_not_buy`.
- `why`: plain English, no IFPP/horizon jargon.

`prefer=auto` **must not** mean cash-first. `prefer=cash` remains an explicit API override for tests/power users. WinUI does not show Prefer.

## Can I buy? loop

### Form (Simple)

| Field | Required | Notes |
|---|---|---|
| Amount | Yes | > 0 |
| Proposed account | Yes | Cash or credit, nicknames |
| Reward category | Yes | Canonical keys: general, gas, groceries, restaurants, travel, amazon, online, drugstore, home_improvement, entertainment, utilities, other |
| Promo | Optional | None · this card’s intro 0% · purchase plan (months and/or monthly $) · I have a new offer → offer desk |
| Budget category | Optional | Existing COA; warning / raid / cuts only |

Remove Prefer combo and “Auto prefers cash” copy.

### Check — `POST /api/pre-purchase`

Advice only. No transaction, no promo line.

Body adds: `reward_category`, `proposed_account_id`, `promo` object. `category_id` stays budget-only. `prefer` defaults to `auto` = ranker (not cash).

### I charged it — `POST /api/pre-purchase/commit`

Requires a prior check payload (or the same fields plus `post_account_id`).

`post_account_id` is either recommended or proposed.

Writes:

- Charge on `post_account_id` (today, −amount, optional payee, optional budget category).
- If path is **purchase plan**: create `kind=purchase_plan` line, `source=user`, `linked_txn_id` set, monthly from months or entered monthly, end from months. Recompute card payment + Safe.
- If path is **card intro**: charge only.
- If cash: cash outflow only.

Unsafe `post_account_id`: same never-neg 409 + `confirm_unsafe` as mark-paid. APR-risk card: 409 with reason; confirm required.

Idempotent on a client `commit_key` (optional) so double-tap does not double-post.

What-if / save scenario stay write-free. What-if may pass `account_id` + promo so the sim matches the check.

## Offer desk

**Where:** Full books → Credit → Offers & promos. Simple: only via Can I buy? “I have a new offer,” plus Home promo brief when an offer is unreviewed or a promo ends within 45 days.

### Offer types

| Type | Enter | Accept writes |
|---|---|---|
| New card | Limit, close/due if known, intro APR + months, annual fee, optional rewards | New credit account + card-intro term. No charge. |
| Balance transfer | From account, amount, fee % or $, 0% months, destination (existing or new) | Fee + transfer (or principal move) + intro/plan on destination. |
| Purchase plan template | Card, months, monthly or split evenly, optional amount | Template / line on the card; next payment includes monthly. |

### Verdict (same ranker + opportunity cost)

- **Take it** — interest-free path is real, pay dates still fit Safe, fee < interest otherwise paid (or float/rewards clearly win).
- **Take it with a plan** — only if pay policy / monthly is set (e.g. do not use `books` / current balance or you kill the 0%).
- **Skip** — would revolve, squeeze Safe, fee > savings, or an existing card already wins.

Always options + why. Accepting Skip requires confirm.

### Manual editor (existing cards)

Add / edit / end purchase plans and card intro. Show `source`. User edits set `source=user`.

## Statement scrape and Plaid

### Statements (required)

PDF / OFX / CSV text extraction **must** find:

| Pattern | Term |
|---|---|
| ISB / Pay over time / installment plan table (name, remaining, monthly, months left or end) | `purchase_plan` |
| Promo / deferred / 0% with balance + end date | `card_intro` (or a named line if multiple) |
| APR table (purchase / special / BT / cash) + balance subject to | Rates + which bucket is 0% |
| Close, due, statement balance, min due | Existing cycle fields |

Today `extract_statement_enrichment` only fills empty **account** promo fields. This spec adds **line-level** parse + upsert.

### Conflict review (D10)

When a scrape or Plaid payload matches a fingerprint whose current `source` is `user` **and** remaining, monthly, end, or APR **differs**:

- Do **not** apply the incoming values.
- Insert `promo_conflicts` row: `{ line_id, field diffs, incoming_values, user_values, incoming_source }`.
- Import / Credit UI: **review prompt** — Keep mine · Take statement (or Plaid) · Edit (saved as `source=user`).
- API: `POST /api/promos/conflicts/{id}/resolve` with `keep_user` \| `take_incoming` \| `edit` + fields.
- Home / after-import brief lists unresolved conflicts (Do this next).

New fingerprints create lines with `source=statement` or `plaid`. Remaining $0 on statement → mark inactive (no review unless `source=user`). Incoming statement may update an existing `source=statement` or `plaid` line without a prompt. Incoming Plaid may update `source=plaid` without a prompt; it may not change `source=statement` or `source=user` without review (statement wins silent ties).

### Plaid

Liabilities: due, last statement, APR list (`purchase_apr`, `special` 0%, `balance_transfer_apr`), balance subject to APR.

- Upsert `card_intro` when a 0% / special bucket has balance.
- Refresh due / close / min / limit as today.
- **Do not** invent Amazon monthly rows from Plaid. Those stay statement + manual.
- Same conflict rule if Plaid would change a `user` line.

### Import UI

After a credit statement: “Promos found: N” (name, monthly, remaining, end) + conflict count. Parse failure is non-fatal. Cycle recompute runs after applied lines and after each conflict resolve.

## Product-wide audit

Replace cash-first and rewards-only picks with `smart_charge`.

| Surface | Required change |
|---|---|
| `pre_purchase.check_purchase` | Ranker; delete cash-first `auto` |
| Buy page | New form; commit buttons; no Prefer |
| `GET /api/rewards/pick` | Delegate to ranker (or return ranker order). Rate cannot beat a tighter pay date |
| Home float whisper | Best card from ranker |
| Credit Best card | Ranker |
| Do this next / `promo_brief` / `books_brief` | Unreviewed promo conflicts; promo ending soon |
| After import | Promos found + conflict review |
| Tray / Glance / `honestspend home --brief` | Safe includes plan monthlies; no “use cash” copy |
| What-if / scenarios | Optional account + promo |
| Setup / discover | Do not seed prefer-cash |
| Docs: PRODUCT, SIMPLE_MODE, BUDGETS, Buy subtitle, CHANGELOG principles | Remove “auto prefers cash” |
| Tests: `test_pre_purchase_cash_first` | Invert: safe card beats cash when float+rewards win |
| Dogfood / northstar | Expect card rec when a safe 0% rewards card exists |

**Unchanged:** never-neg gate, envelope raid, budget reserve formula, tax vault, payroll package, MSIX/Python embed.

**Audit done when:**

1. `prefer=auto` never returns cash solely because Safe covers and a safe card exists.
2. A statement with an Amazon-style plan changes next payment without a manual line.
3. User vs statement conflict blocks apply until review.
4. PRODUCT / SIMPLE / BUDGETS / Buy copy do not say cash-first.
5. Dogfood/northstar assert the card rec in the rewards+float fixture.

## API sketch

| Method | Path | Role |
|---|---|---|
| POST | `/api/pre-purchase` | Check (advice) |
| POST | `/api/pre-purchase/commit` | I charged it |
| GET | `/api/rewards/pick` | Ranker-backed |
| GET/POST | `/api/offers` | List / create pending offers |
| POST | `/api/offers/{id}/decide` | take \| take_with_plan \| skip |
| GET/POST/PATCH | `/api/accounts/{id}/promo-lines` | Manual editor |
| GET | `/api/promos/conflicts` | Pending statement/Plaid vs user |
| POST | `/api/promos/conflicts/{id}/resolve` | keep_user \| take_incoming \| edit |

Existing cycle recompute stays the single writer of `Card payment · {nickname}`.

## UI copy

| Avoid | Say |
|---|---|
| Auto prefers cash | (delete) |
| IFPP / horizon / runway | Safe to spend, next payment, next risk day |
| Use cash | Charge **{nickname}** — {float days} interest-free, {rate}% {category} |
| Take statement silently | This statement doesn’t match the promo you entered |

## Tests (minimum)

- Ranker: safe 5% groceries card with due in 25 days beats checking with Safe >> amount.
- Ranker: card that would make next payment miss Safe loses to cash.
- Ranker: two safe cards → more float days wins; equal float → higher category rate.
- Missing due day → card unsafe + tip.
- Commit recommended vs proposed; unsafe proposed 409 then confirm.
- Purchase-plan commit creates line; next payment += monthly.
- Statement parse: Amazon-like table → purchase_plan; next payment changes.
- Statement vs user line → conflict, not overwrite; resolve take_incoming updates remaining.
- Plaid special 0% upserts card_intro; does not invent plan rows.
- Offer BT: Skip when fee > estimated interest saved; Take when opposite and Safe fits.
- Regression: `prefer=auto` is not cash-first.

## Key decisions (summary)

1. One ranker: safety → float → rewards. Cash is last among safe options.
2. Extend existing ISB + intro fields; offers are pending terms until accept.
3. Check vs commit split; post-time choice of recommended vs proposed.
4. Reward category required; budget optional and non-picking.
5. Statements must parse plan tables; conflicts with user lines require a review prompt.
6. Audit is part of ship, not a sequel.

## Open questions

None remaining from design review. Implementation may discover issuer-specific parse fixtures; add them as tests, do not change ranker rules.

## PR Plan

Work stays on **main**. Each PR is independently reviewable; later PRs depend on the ranker + term fields.

### PR1 — Promo term fields + scrape + conflict review

- **Affects:** `db.py` / migration, `promo_installments.py`, `import_bootstrap.py`, `statement_pdf.py`, import WinUI, APIs for lines + conflicts.
- **Depends on:** none.
- **Does:** `kind` / fingerprint / offer status; parse ISB/plan tables; upsert; user-vs-statement review prompt; cycle recompute after apply.

### PR2 — `smart_charge` ranker + pre-purchase check

- **Affects:** new `smart_charge.py`, `pre_purchase.py`, `rewards_pick.py`, tests (replace cash-first), PRODUCT/SIMPLE/BUDGETS copy for the rule.
- **Depends on:** PR1 fields (intro + plan monthlies in next-payment sim).
- **Does:** New rank; `prefer=auto` is ranker; rewards/pick delegates.

### PR3 — Can I buy? WinUI loop + commit

- **Affects:** `BuyPage.xaml(.cs)`, `LedgerApiClient`, `POST /api/pre-purchase/commit`, what-if optional account.
- **Depends on:** PR2.
- **Does:** New form; Check; I charged recommended / original; purchase-plan write.

### PR4 — Offer desk

- **Affects:** Credit Offers page, offer APIs, Home promo brief for unreviewed offers / ending promos.
- **Depends on:** PR1–PR2.
- **Does:** New card / BT / plan template; verdict; manual editor; Simple door from Can I buy?.

### PR5 — Plaid promo upsert + product-wide audit

- **Affects:** `plaid_service`, Home whisper, Credit best-card, tray/Glance/CLI copy, dogfood/northstar, remaining docs.
- **Depends on:** PR1–PR4.
- **Does:** Plaid `special` → card_intro (conflict rule); every listed call site uses the ranker; audit done-when checks green.
