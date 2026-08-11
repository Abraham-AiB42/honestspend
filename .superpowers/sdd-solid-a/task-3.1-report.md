# Task 3.1 Report: Credit progressive disclosure

**Status:** DONE  
**Branch:** `feature/statement-cycles`  
**Commit:** `46e814b` - `feat(ui): Credit progressive disclosure`  
**Date:** 2026-08-11

## Goal

Credit Full desk progressive disclosure: Statement & payment first; promo / freeze / util / score / payoff / rewards collapsed by default. One pay-policy editor only (no dual Autopay chrome).

## Change

### `clients/HonestSpend.WinUI/Pages/CreditPage.xaml`

Reordered into three tiers:

| Tier | Content | Default |
|------|---------|---------|
| **1 — Always open** | Card summary list · Card picker · Statement & payment (close/due/policy/timing/fixed/funding) · projected / next payment · honesty one-liner (min / interest / util) · **Save payment settings** + Recompute | Expanded |
| **2 — Expanders** | Promo plans & calendar · Statement freeze · Utilization pay-to-target (30%/10% chips + credit advice list) | Collapsed |
| **3 — Learn & tools** | Educational score · self-entered history · score factors / suggestions / what-if · payoff plan + compare · rewards pick | Collapsed |

- Primary CTA renamed to **Save payment settings**.
- Page subtitle points users at expanders for secondary tools.
- Deleted dual Autopay section entirely (`AutopayList` / collapsed ComboBoxes / Save pay policy button) — card policy summary already lives in `CycleSummaryList`.

### `clients/HonestSpend.WinUI/Pages/CreditPage.xaml.cs`

- Removed `LoadAutopayAsync` and `Autopay_Click`.
- Dropped calls from `LoadAsync` and `CycleSave_Click`.
- All remaining named controls unchanged for load/save/promo/freeze/rewards/plan paths.

## Verify

```
dotnet build clients/HonestSpend.WinUI/HonestSpend.WinUI.csproj -c Debug -p:Platform=x64
```

**Result:** Build succeeded, 0 warnings, 0 errors.

## Constraints check

| Constraint | OK |
|------------|----|
| Statement & payment first paint | yes |
| Promo / freeze / util collapsed | yes |
| Score / payoff / rewards in Learn & tools | yes |
| One pay-policy editor only | yes (Statement & payment) |
| Dead dual Autopay deleted (not Visibility=Collapsed) | yes |
| WinUI build green | yes |

## Files

1. `clients/HonestSpend.WinUI/Pages/CreditPage.xaml`
2. `clients/HonestSpend.WinUI/Pages/CreditPage.xaml.cs`
3. `.superpowers/sdd-solid-a/task-3.1-report.md`
4. `.superpowers/sdd-solid-a/progress.md`
