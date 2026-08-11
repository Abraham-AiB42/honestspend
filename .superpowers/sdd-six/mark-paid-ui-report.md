# Mark paid UI — report

**Branch:** feature/statement-cycles  
**Commit:** `2f9516c` feat(winui): mark paid from Coming up  
(Home Coming up pick UI co-landed in `dd3461a` with CardFix)

## Delivered
1. `LedgerApiClient.MarkSchedulePaidAsync(id, createTransaction=true)` → `POST /api/scheduled/{id}/mark-paid`
2. Simple Home Coming up: `ComingUpPayPick` ComboBox (outflows only, `Tag=scheduled_id`) + **I paid this**
3. On success: `RefreshAsync()` + `ShowSuccess`; Coming up list/totals reload
4. Inflows excluded (`direction != "out"` or missing `scheduled_id`)

## Verify
`dotnet build clients/HonestSpend.WinUI -c Release -p:Platform=x64` → succeeded
