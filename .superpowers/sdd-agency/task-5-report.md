# Task 5 Report

**Status:** Done · **Commit:** `93ff935` `feat(winui): bill series, vendor, pay-from, owner draw`

**Changes:**
- `LedgerApiClient`: `GetScheduledSeriesAsync`, `AddSeriesStepAsync`
- `BillsPage`: Kind Bill/Income/Owner draw; Pay from (cash|credit); Vendor; Starts/Ends; Opex (business); Income source; **Change amount on date…** → series step; auto `series_id` on create
- `MoneyWizardPage`: same agency fields + `owner_draw` wizard path
- `AddHubPage`: Owner draw entry

**Build:** Release x64 succeeded (0 warnings/errors)
