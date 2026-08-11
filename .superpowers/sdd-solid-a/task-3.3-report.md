# Task 3.3 Report: Settings plain never-neg / pending labels

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `65019bc` - `fix(ui): plain Settings never-neg and pending labels`  
**Date:** 2026-08-11

## Goal

Settings UI labels for never-neg enforcement and strict pending use plain English -- no IFPP / engine jargon on the user-visible path.

## Change

### `clients/HonestSpend.WinUI/Pages/SettingsPage.xaml`

| Control | Before | After |
|---------|--------|--------|
| **NeverNegEnforceBox** items | Off (just warn later) / Ask me first / Always block | **Off -- allow negative checking** / **Warn -- ask before going negative (recommended)** / **Hard -- never allow negative checking** |
| Never-neg subtitle | (none) | Explains Off vs Warn vs Hard in one line |
| **ScopeBox** header | Never-negative pool | **Which accounts must stay non-negative** |
| Scope items | checking only / all cash... | Checking only / All cash accounts |
| **ClearedOnlyBox** | Flag pending before trusting books | **Treat pending as already spent** |
| Pending subtitle | (none) | On: pending reduces Safe to spend; off: warning only |
| Money view items | This money (silo) / All money (combined) | **This money** / **All money** (no silo/combined jargon) |

Tags (`off` / `warn` / `hard`, `checking` / `all_cash`, etc.) and API field names (`never_negative_enforcement`, `ifpp_cleared_only`, `ifpp_scope`) unchanged -- only labels.

### `clients/HonestSpend.WinUI/Pages/SettingsPage.xaml.cs`

- Save status: dropped "PATCH" / "cleared-only" jargon -> "buffer, protection, and pending treatment applied."

### Grep (WinUI Pages user-visible IFPP)

- No user-visible "IFPP" strings in XAML.
- Code-only: property names, API paths, comments (`is_cash_for_ifpp`, `GetIfppAsync`, etc.) -- not Simple-path labels.
- Accounts **Count toward Safe to spend** already plain via `UiCopy.SpendableCash`.

## Verify

```
dotnet build clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj -c Debug -p:Platform=x64
```

**Result:** Build succeeded, 0 warnings, 0 errors.

## Constraints check

| Constraint | OK |
|------------|----|
| never_neg Off/Warn/Hard plain English | yes |
| Subtitle explaining modes | yes |
| ifpp_cleared_only -> "Treat pending as already spent" | yes |
| No IFPP in Settings labels | yes |
| Engine tags / API keys unchanged | yes |
| WinUI build green | yes |

## Files

1. `clients/HonestSpend.WinUI/Pages/SettingsPage.xaml`
2. `clients/HonestSpend.WinUI/Pages/SettingsPage.xaml.cs`
3. `.superpowers/sdd-solid-a/task-3.3-report.md`
4. `.superpowers/sdd-solid-a/progress.md`
