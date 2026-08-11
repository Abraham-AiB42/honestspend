# Rewards rate chips on card setup — report

## Status
**Complete**

## Deliverable
1. **Helper** `Helpers/RewardsRatesUi.cs` — presets (Gas 5%/1%, Amazon 5%, Home Depot 5%, Groceries/Dining, Custom) + chips + NumberBoxes (`gas`, `groceries`, `restaurants`, `amazon`, `home_improvement`, `general`)
2. **AccountsPage** — create form shows rewards when Kind=credit; edit dialog loads via `GetRewardsRatesAsync` and saves via `PutRewardsRatesAsync`
3. **MoneyWizardPage** (card) — last step “Autopay & rewards”; PUT after `CreateAccountAsync`
4. **FirstRunPage** — card fields include rewards; after first-run, resolve card by nickname and PUT rates
5. **LedgerApiClient** — `GetRewardsRatesAsync` added (Put already present)

## Commit
`feat(winui): rewards rate chips on card setup`

## Files
- `clients/HonestSpend.WinUI/Helpers/RewardsRatesUi.cs` (new)
- `clients/HonestSpend.WinUI/Pages/AccountsPage.xaml` / `.xaml.cs`
- `clients/HonestSpend.WinUI/Pages/MoneyWizardPage.xaml.cs`
- `clients/HonestSpend.WinUI/Pages/FirstRunPage.xaml.cs`
- `clients/HonestSpend.WinUI/Services/LedgerApiClient.cs`
- `.superpowers/sdd-six/rewards-chips-report.md`

## Build
`dotnet build` WinUI x64 Debug — **0 errors**
