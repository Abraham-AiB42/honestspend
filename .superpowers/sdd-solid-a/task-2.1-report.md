# Task 2.1 Report: Risk day plain weekday dates (Simple)

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit message:** `fix(ui): plain weekday risk day on Home`  
**Date:** 2026-08-11

## Goal

Home RiskLine shows next risk day as plain English date like Coming up (`Fri Mar 6`), not ISO `2026-03-06`.

## Change

### `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`

1. **RiskLine source = `next_risk_day` only**  
   Removed the optional cash-runway override that replaced the home IFPP risk day with runway `first_red_day` / horizon copy (dual cash story). Risk line stays on the same spine as Safe to spend.

2. **Plain weekday format**  
   `FormatPlainWeekdayDate` parses ISO `yyyy-MM-dd` (or round-trip DateTime) with `CultureInfo.InvariantCulture` and formats as `ddd MMM d` → e.g. `Fri Mar 6`. Unparseable strings pass through.

3. **Empty / missing**  
   Still: `No near-term cash crunch` (no ISO, no "day" suffix).

4. **Present risk**  
   `Next risk day: Fri Mar 6 — cash may run short around then`

## Verify

```
dotnet build clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj -c Debug -p:Platform=x64
```

**Result:** Build succeeded, 0 warnings, 0 errors.

## Constraints check

| Constraint | OK |
|------------|----|
| Plain weekday like Coming up (`ddd MMM d`) | yes |
| Empty → "No near-term cash crunch" | yes |
| Risk from home `next_risk_day` (not dual runway) | yes |
| No Task 2.2 Coming up expand | yes |
| Commit message as specified | yes |

## Files committed

1. `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`
2. `.superpowers/sdd-solid-a/task-2.1-report.md`
3. `.superpowers/sdd-solid-a/progress.md`

## Notes / follow-ups

- Task 2.2 (Coming up expand / full window) may touch HomePage again — not done here.
- Helper is private static on HomePage; could move to Helpers if Task 2.2 needs shared date formatting.
