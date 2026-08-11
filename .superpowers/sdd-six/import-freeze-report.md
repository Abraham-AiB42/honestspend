# Import freeze statement — report

**Branch:** `feature/statement-cycles`  
**Commit:** `feat(winui): prompt freeze statement after credit import`

## What shipped

After CSV/OFX/PDF (and inbox credit hits) import, credit accounts get a post-import panel: *Got a statement balance from the bank? Freeze it for this close.* NumberBox + Freeze → `FreezeStatementCycleAsync` (`source: import`). Prefills from `ending_balance` / `ledger_balance` / `institution_balance`. Cash imports unchanged.

## Files

| Path | Change |
|------|--------|
| `clients/.../ImportPage.xaml` | Freeze panel (InfoBar + NumberBox + Freeze/Not now) |
| `clients/.../ImportPage.xaml.cs` | Kind map, offer/prefill/freeze/dismiss |

## Notes

- Uses existing `POST …/statement-cycles/freeze` via `LedgerApiClient.FreezeStatementCycleAsync`
- Cash path: panel stays collapsed when account kind ≠ credit
