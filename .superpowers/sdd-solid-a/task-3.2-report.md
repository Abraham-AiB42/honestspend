# Task 3.2 Report: Plain power copy — no raw #ids

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `13744a0` - `fix(ui): plain power copy without schedule ids`  
**Date:** 2026-08-11

## Goal

User-visible success strings must not show raw schedule `#id`s.

## Find

Grep WinUI Pages for `#{` / `net_id` / `tax_id` / `schedule #` in user messages.

| Location | Finding |
|----------|---------|
| `BillsPage.xaml.cs` payroll success | **Hit** — `net #{net_id} · tax #{tax_id}` + truncated `package_id` |
| Other Pages | No user-visible raw schedule `#id` strings |

## Change

### `clients/HonestSpend.WinUI/Pages/BillsPage.xaml.cs`

`PayrollPackage_Click` success copy:

**Before:**
```text
Payroll day created · net #12 · tax #13 · package abcd1234…
```

**After:**
```text
Created payroll day · net $X.XX + tax $Y.YY
```

Uses the same `net` / `tax` amounts already entered for the create call — no schedule or package ids on the happy path.

## Verify

```
dotnet build clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj -c Debug -p:Platform=x64
```

**Result:** Build succeeded, 0 errors (1 pre-existing SettingsPage.g.cs CS1697 checksum warning).

Post-fix grep for `#{` / `net_id` / `tax_id` under `clients/HonestSpend.WinUI`: **no matches**.

## Constraints check

| Constraint | OK |
|------------|----|
| No raw schedule `#id`s in user success strings | yes |
| Payroll: `Created payroll day · net $X + tax $Y` | yes |
| WinUI build green | yes |

## Files

1. `clients/HonestSpend.WinUI/Pages/BillsPage.xaml.cs`
2. `.superpowers/sdd-solid-a/task-3.2-report.md`
