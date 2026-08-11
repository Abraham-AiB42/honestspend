# Statement cycles (credit cards)

Excel **Budget.xlsx** parity for credit: close day, due day, running books balance, and a live **next payment** — without a day-grid sheet. Multi-entity books, promo installment lines, and cash-funded pay-from make Safe to spend honest.

## Why not a day grid?

The owner’s spreadsheet tracked every day on a **Credit Cards** tab (`running = prior + activity`) and hard-coded due cells on **Budget**. HonestSpend does the same job better:

| Excel | HonestSpend |
|-------|-------------|
| Day-by-day grid per card | One **ledger** of transactions (import / Plaid / manual) |
| Header balance = sum to today | **Books balance** rebuilt from non-void txns |
| Static due-day formulas | **Projected statement** + **next payment** recomputed on every balance change |
| ISB tab for 0% lines | **Promo installment lines** on the card |
| One personal workbook | **Multi-entity** silos (Personal / Business / Child) with group view optional |

No client-side balance math. The engine owns cycle windows, payment amount, and the cash schedule Safe to spend reads.

## Plain language (user-facing)

| Say | Meaning |
|-----|---------|
| **Safe to spend** | Cash you can use without bouncing checking (after bills, buffers, budget reserve, tax vault, …) |
| **Statement balance** | What we expect the open cycle to bill (books balance minus open promo principal still on plan) |
| **Next payment** | Cash that should leave the **pay-from** account on the next due date under the card’s pay policy |
| **Pay from** | Checking/cash account that funds the card payment |
| **Close day / Due day** | Statement closes this day of month; payment is due this day of month |

Never expose engine jargon (runway formulas, schedule notes tags) in UI copy.

## Model

### Cycle windows

For a credit account as of today:

1. **Last close** — most recent statement close on or before today (`statement_close_day`, clamped to month end).
2. **Next close** — first close strictly after today.
3. **Next due** — next payment due date from `payment_due_day`.

### Projected statement & next payment

```
promo_remaining = sum of open promo line principal
promo_due       = sum of this-cycle monthly installments (capped by remaining)

statement balance = max(0, books balance − promo_remaining)

policy "Pay statement in full":
  next payment = max(0, books − promo_remaining) + promo_due
policy "Minimum only":
  next payment = card min payment (or ~2% / $25 floor)
policy "Fixed amount":
  next payment = fixed amount you set
policy "0% promo monthly set-aside":
  next payment = promo_due only
policy "None":
  next payment = 0 (no cash schedule)
```

Intentional **0% float** stays first-class: statement-in-full does **not** force paying the whole promo principal when installment lines are open — only the non-promo portion plus this month’s installment.

### Cash-funded payment schedule

- Name: `Card payment · {nickname}`
- Lives on the **pay-from** cash account (not the card)
- Amount = **−next payment**, date = next due
- Safe to spend **does** reserve this cash outflow
- Card charge / recurring expense schedules on the credit account **do not** double-count against cash

### Config priority

| Source | Behavior |
|--------|----------|
| **You** (Full books Credit / Accounts) | `cycle_config_source=user` — imports never overwrite close/due/funding/policy |
| **Import / Plaid / discover** | Fill **missing** fields only |
| **Defaults** (new card) | Close day 1 · due day 15 · pay statement in full · funding = preferred checking |

### Running books balance

- Display and payment math use `Account.current_balance` after every apply.
- `rebuild_running_balance` can re-sum the ledger if books drift — no parallel day-grid store.

### Promo installment lines

Like the spreadsheet ISB tab, per card:

| Field | Role |
|-------|------|
| Name | e.g. “Amazon 0%” |
| Principal remaining | Still on the promo plan |
| Monthly payment | Set-aside / installment this cycle |
| Active + date window | Open lines only count |

Create / edit / roll one month from Full books **Credit**. Rolling reduces principal by one installment and recomputes next payment.

### Multi-entity

Each card belongs to a **Who** (entity). Cycle config, promo lines, and cash pay-from stay siloed. Home **This money / All money** still uses the same Safe to spend rules; card payment schedules hit the funding cash account for that entity.

### History (`statement_cycles`)

Optional cycle history rows (projected / later import of actual statement). v1 payment math is **live** from books + promo lines; imported actual balances can freeze a closed cycle later.

## Recompute hooks

After any credit books change, the engine runs **recompute** for that card:

- Create / patch / void transaction  
- CSV / OFX / PDF import, Plaid sync  
- Cycle config save, promo line create/edit/roll  
- Manual **Recompute** on Credit  

Recompute:

1. Projects statement balance + next payment  
2. Writes caches on the account  
3. Upserts or ends the cash `Card payment · …` schedule  

Force: `POST /api/accounts/{id}/recompute-cycle`.

## API (summary)

| Route | Purpose |
|-------|---------|
| `GET /api/accounts/cycles` | All cards’ projections for current Who |
| `GET /api/accounts/{id}/cycle` | One card projection |
| `PUT /api/accounts/{id}/cycle-config` | Close / due / policy / fixed amount / pay-from (marks source = user) |
| `POST /api/accounts/{id}/recompute-cycle` | Force recompute |
| `GET/POST /api/accounts/{id}/promo-lines` | List / add installment lines |
| `PATCH …/promo-lines/{line_id}` | Edit remaining / monthly / name |
| `POST …/promo-lines/{line_id}/roll` | Apply one month against principal |

Account list also returns **cached** statement balance, next payment amount, and next payment date.

## UI

| Mode | Surface |
|------|---------|
| **Simple** | Home whisper only when a card payment is due within **14 days**: “Next card payments: $X on {date}”. No day grid, no cycle editor. |
| **Full books → Credit** | **Statement & payment**: close/due, policy, pay-from, projected statement, next payment, Save, Recompute; promo lines list + add/edit/roll |
| **Full books → Accounts** | Credit row meta (close day, next pay / statement); edit dialog can set close/due |

## Trust bar

- Never bounce checking — never-neg runs when cash actually pays, not when the schedule is projected.  
- No accidental APR — promo carve-out + pay-from cash path keep 0% float intentional.  
- One honest Safe to spend number everywhere (Home, tray, glance, Can I buy?).  
