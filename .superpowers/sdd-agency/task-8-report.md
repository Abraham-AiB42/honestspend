# Task 8 Report

**Status:** Done · **Commit:** `2d55770` `feat: entity year P&L report`

**Changes:**
- `src/financial_os/services/entity_pnl.py` — `build_entity_pnl(session, profile_id, year)`
  - Buckets (signed): `income`, `payroll`, `taxes`, `owner_draws`, `expenses`, `net`, `by_category`
  - Rules: category `budget_group` / `tax_form` + payee hints (`payroll`, `package net`, `employer tax`, owner draw); transfers skipped
- API: `GET /api/reports/entity-pnl?profile_id=&year=`
- WinUI Reports: Entity year P&L card (selected shell entity, current year)
- Tests: `tests/test_entity_pnl.py` (unit buckets + name hints + API)

**Tests:** 3 passed (`test_entity_pnl.py`)
