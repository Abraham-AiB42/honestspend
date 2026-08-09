# HonestSpend — Product Constitution

Locked decisions from product discovery (2026). This is the source of truth for roadmap and defaults.

---

## One-liner (proposed — refine later)

> **HonestSpend is the open-source liquidity cockpit for small business owners who refuse to go negative, pay dumb fees, or waste money on unnecessary interest.**

*(Owner had no preferred one-liner yet; this matches locked priorities. Revisit anytime.)*

---

## Mission

| Axis | Decision |
|------|----------|
| **Audience** | Small business owners (open source); free for anyone |
| **License / commercial** | **Freeware** — open source; anyone can use |
| **Category** | Non-investment personal + multi-entity **cash, credit float, debt math, tax-ready books** |
| **Primary metric** | Fiscally sound decisions (#1); credit health (#2) |
| **Daily effort** | **Minimum possible** — automation does the work |

---

## Non-negotiables (trust bar)

1. **Never go negative on checking** — ever.  
2. **Fees are stupid** — detect, warn, avoid.  
3. **Unnecessary interest is stupid** — never revolve at APR by accident.  
4. **Intentional 0% float is a first-class strategy** — not a bug; charge when payoff path is interest-free.  
5. **Opportunity cost is real** — e.g. don’t prepay 2.625% mortgage when cash yields 6%.  
6. **Recommendations always include options + reasoning** — user can override.

---

## Spendable / IFPP model

| Setting | Decision |
|---------|----------|
| Checking | **Never negative** (hard constraint) |
| 0% card float | **Allowed and encouraged** when fiscally sound (grace/promo path proven) |
| Safety buffer | User choice; **default $1,000**; budgeting targets can go “to infinity” |
| Entity liquidity | **Siloed Spendable per entity (default)** + optional **Combined/group** view (`scope=entity|group` on IFPP; WinUI Home switcher) |
| Variable income | **Optional, fully configurable** commission/income cliffs — not forced |
| Priority of advice | (1) fiscal soundness (2) credit score |

---

## Integrations

| Area | Decision |
|------|----------|
| **Licensing** | **Full freeware** — no paid tiers, no hosted bank feed we bill for |
| Bank / cards | **CSV/OFX (+ statements) first** · customizable import reminders · optional **BYOK Plaid** |
| Source of truth | **Local books** day-to-day; bank exports / optional Plaid keep them honest |
| Payroll | **No** payroll product integrations — enter straight numbers |
| Tax geography | **Multi-state** ready (tax lines / settings; not single-state locked) |
| AI | **Rules first** · optional **BYOK Grok** (`FOS_XAI_API_KEY`) — never required |

### Money-in (freeware)

1. First-run: pick **how often** to remind (off / daily / weekly / monthly) and **what** (transactions / statements / both).  
2. User downloads files from their bank site (we never store bank passwords).  
3. Import → clock resets · Home/digest nags only when due.  
4. Optional: user brings **their** Plaid keys for live link; optional **their** xAI key for AI categorize.

---

## Platforms

| Now | Inevitable |
|-----|------------|
| Windows-first local | **Windows, Mac, Linux, iOS, Android** clients |

Architecture must not hard-lock to Windows-only (local API + multi-client later).

---

## Data & privacy

| Topic | Decision |
|-------|----------|
| Runtime | Local-first |
| Backup | **Local** (user copies/encrypts folder; OneDrive-of-DB optional by user) |
| Sharing | **Permission stack** (roles: owner, bookkeeper, CPA view-export, etc.) — not “everyone sees everything” |

---

## Explicitly out of scope (for now)

- Investment / brokerage trading product (rescue coach may advise sell-to-cash only)  
- Affiliate loan/card marketplaces (Credit Karma model)  
- Payroll system connectors  
- Live multi-writer SQLite on cloud sync folders (use encrypted snapshots instead)  

---

## Entities (public model)

| Entity | How | IFPP |
|--------|-----|------|
| **Personal** | Seeded by default | Silo Spendable |
| **Business(es)** | Add Business — user names; tax form 1120S/1065/SchC/… | Silo Spendable |
| **Child(ren)** | Add Child — allowance COA, not a filing entity | Silo Spendable |

Combined (group) view is an explicit toggle. Never hardcode private business names in product.

---

## Entity money events (owner ops)

When personal and business (or child) money crosses:

| Event | Direction |
|-------|-----------|
| Owner distribution | Equity / transfer — not expense |
| Reimbursement | Pair personal charge → business repayment |
| Capital inject | Personal → business |
| Child allowance | Personal → child transfer |

Default: **fiscal clarity first** + clear Spendable impact per entity silo.

---

## Open source / community

- Intended for **small business owners** broadly  
- **Freeware** — no paid gate for core  
- Product should stay **self-hostable / local** as the default trust model  

---

## Success criteria

A user can open the app **rarely** and still:

- Never bounce checking  
- Never pay avoidable interest or fees  
- Park money where it earns more than cheap debt costs  
- Keep multi-entity books tax-exportable  
- Get **recommended action + alternatives + why** in one glance  

---

## Defaults (engineering)

| Setting | Default |
|---------|---------|
| `safety_buffer` | **1000** |
| `never_negative_scope` | **checking** |
| `opportunity_cost_aware` | **true** |
| `debt_strategy` | **avalanche** (among debts above opportunity hurdle) |
| 0% float in IFPP | **include** interest-free card capacity when payoff path exists |
| Commission cliffs | **off** until user configures (`income_cliff_enabled`) |
| Tax vault | **enabled**, balance **$0** until funded; reduces Spendable |
| API auth | Optional `X-API-Key`; no key = local owner |

---

## Answer index (raw)

1. Open source for SMB owners  
2. No idea (one-liner) → proposed above  
3. Freeware for anyone  
4. Never checking negative; intentional 0% float is strategy  
5. Do what makes sense (entity Spendable)  
6. Budgeting → infinity; safety net user choice; default $1k  
7. Options + recommendations with reasoning  
8. Fiscal #1, credit score #2  
9. Most fiscally sound promo usage  
10. Live integrations as truth where possible  
11. No payroll integrations — numbers only  
12. Multi-state  
13. Max live integration  
14. Never negative; no stupid fees; no unnecessary interest  
15. Local backup  
16. Minimal user time — max automation  
17. Win/Mac/Linux/iOS/Android inevitable  
18. Permission stack for multi-party access  
19. Best-practice intermix tools (TBD implement)  
20. Commission cliffs optional + fully configurable  
21. Children — first-class siloed entities (Add Child); allowance books, not tax filing  

