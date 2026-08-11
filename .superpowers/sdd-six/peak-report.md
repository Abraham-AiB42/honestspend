# Peak books balance (Full books) — report

## Status
**Complete**

## Deliverable
1. **Service** `src/financial_os/services/account_peak.py` — `peak_balance(session, account_id, *, as_of=None, lookback_days=90)`
   - Walks non-void / non-pending txns with same sign rules as `apply_amount_to_account`
   - Returns: `current`, `peak_lookback`, `peak_lookback_date`, `peak_open_cycle` (credit + close day: last close → as_of), `lookback_days`
2. **Tests** `tests/test_account_peak.py` — charges raise peak; void/pending excluded
3. **API** `GET /api/accounts/{id}/peak?lookback_days=90` (in `app.py`; already on branch from concurrent commit `4ffc2a1`)
4. **CreditPage** — under cycle projection: `Peak last 90d: $X · this cycle: $Y` when cycle loaded

## Commit
`feat: peak books balance for credit cards`

## Suite results
```
.venv\Scripts\pytest.exe tests/test_account_peak.py -v --tb=short
```
**2 passed**

## Files staged
- `src/financial_os/services/account_peak.py` (new)
- `tests/test_account_peak.py` (new)
- `clients/HonestSpend.WinUI/Pages/CreditPage.xaml`
- `clients/HonestSpend.WinUI/Pages/CreditPage.xaml.cs`
- `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- `.superpowers/sdd-six/peak-report.md`

## Note
API route was already present on HEAD (bundled into `feat: mark scheduled expense paid`); this commit supplies the service, tests, and WinUI wire-up.
