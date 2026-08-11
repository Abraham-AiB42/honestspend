# Mark paid — report

**Branch:** feature/statement-cycles  
**Commit:** feat: mark scheduled expense paid

## Delivered
1. `src/financial_os/services/schedule_mark_paid.py` — `mark_schedule_paid(session, scheduled_id, *, as_of=None, create_transaction=True)`: active expense only; optional cleared txn + `apply_amount_to_account`; advance via `next_occurrence`; end if past `end_date` (`ended_reason='paid through end'`).
2. `tests/test_schedule_mark_paid.py` — monthly advance, txn+books, skip txn, overdue base, paid-through-end, income reject, API.
3. `POST /api/scheduled/{id}/mark-paid` body optional `{create_transaction, as_of}` — no HomePage changes (wave 2).

## Verify
`pytest tests/test_schedule_mark_paid.py` → 7 passed
