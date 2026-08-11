# Task 2.4 Report: Shared never-neg 409 WinUI helper

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `76b1957` - `refactor(ui): shared never-neg 409 helper`  
**Date:** 2026-08-11

## Goal

One shared parse + soften path for engine never-neg 409 (`would_go_negative`) used by Home Coming up mark-paid and Bills mark-paid.

## Change

### `clients/HonestSpend.WinUI/Helpers/NeverNegUi.cs` (new)

- `TryParseWouldGoNegative` - parse 409 / `would_go_negative` payload; **`confirmRequired` only when JSON `confirm_required` is true** (warn). Hard mode -> false (no dialog).
- `SoftenWouldGoNegativeMessage` - strip "Analyze rescue..." / `POST /api/` jargon.
- `FriendlyMessage` - empty -> default "This would make checking negative."

### `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`

- `ComingUpMarkPaid_Click` catches never-neg via helper; hard -> ErrorBar only; warn -> ContentDialog then `confirmUnsafe: true` retry.
- Coming up expand logic untouched.

### `clients/HonestSpend.WinUI/Pages/BillsPage.xaml` + `.xaml.cs`

- Mark paid button on active expense outflows; shared never-neg path via `NeverNegUi` (same hard/warn behavior).
- SuccessBar for mark-paid / match messaging.

### `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`

- `MarkSchedulePaidAsync(..., confirmUnsafe: false)` -> posts `confirm_unsafe`.

## Behavior preserved

| Case | Result |
|------|--------|
| `confirm_required: true` (warn) | Dialog; Yes retries with confirmUnsafe |
| hard / no confirm_required | No dialog; friendly ErrorBar |
| Soften | Rescue / POST API jargon stripped |

## Verify

```
dotnet build clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj -c Debug -p:Platform=x64
```

**Result:** Build succeeded, 0 warnings, 0 errors.

## Constraints check

| Constraint | OK |
|------------|----|
| confirmRequired only if JSON true | yes |
| hard mode: no dialog | yes |
| Soften strips rescue jargon | yes |
| Home + Bills use helper | yes |
| Do not change Coming up expand | yes |
| WinUI build green | yes |

## Files

1. `clients/HonestSpend.WinUI/Helpers/NeverNegUi.cs`
2. `clients/HonestSpend.WinUI/Pages/HomePage.xaml.cs`
3. `clients/HonestSpend.WinUI/Pages/BillsPage.xaml`
4. `clients/HonestSpend.WinUI/Pages/BillsPage.xaml.cs`
5. `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
6. `.superpowers/sdd-solid-a/task-2.4-report.md`
7. `.superpowers/sdd-solid-a/progress.md`
