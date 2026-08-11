# Task 2.2 Report: Coming up “Show all in window” on Simple Home

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `68e1c30` — `feat(ui): Coming up expand full window on Simple Home`  
**Date:** 2026-08-11

## Goal

When Coming up is truncated (shown &lt; full window count), user can expand to all cash-side items in the window without leaving Simple. Mark paid picker includes expanded outflows.

## Change

### `clients/HonestSpend.WinUI/Pages/HomePage.xaml`

- HyperlinkButton `ComingUpShowAllBtn` under totals (collapsed by default).

### `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`

1. **Detect truncation** from `coming_up.truncated` + `count` &gt; `shown_count`.
2. **Show all (N)** when truncated; click loads `GET /api/coming-up?limit=…` (cap 50) via existing `GetComingUpAsync`.
3. Re-bind full `items` with `BindComingUp(..., expanded: true)`; button becomes **Show less**.
4. **Show less** re-binds embedded `home.coming_up` strip (top 8).
5. **ComingUpPayPick** rebuilt from currently bound items (all window outflows when expanded).
6. Refresh resets expand state. Risk day plain weekday (Task 2.1) untouched.

## Verify

```
dotnet build clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj -c Debug -p:Platform=x64
```

**Result:** Build succeeded, 0 warnings, 0 errors.

## Constraints check

| Constraint | OK |
|------------|----|
| Expand in-place (no Full mode) | yes |
| Show all (N) when fullCount &gt; shown | yes |
| Show less collapses to strip | yes |
| Mark paid includes expanded outflows | yes |
| Preserve Task 2.1 risk day plain dates | yes |
| Commit message as specified | yes |

## Files committed

1. `clients/HonestSpend.WinUI/Pages/HomePage.xaml`
2. `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`
3. `.superpowers/sdd-solid-a/task-2.2-report.md`
4. `.superpowers/sdd-solid-a/progress.md`

## Notes / follow-ups

- Expand uses client defaults for window mode (same as `GetComingUpAsync`); if Settings override diverges from home embed, rare mismatch until client omits mode params.
- API max list size remains 50 (product cap).
