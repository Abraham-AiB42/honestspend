using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LedgerRing_WinUI.Pages;

public sealed partial class BuyPage : Page
{
    public BuyPage()
    {
        InitializeComponent();
    }

    private async void Check_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (App.Backend is not null)
                await App.Backend.EnsureRunningAsync();

            var prefer = "auto";
            if (PreferBox.SelectedItem is ComboBoxItem item && item.Tag is string t)
                prefer = t;

            var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
            using var api = new LedgerApiClient();
            var res = await api.PrePurchaseAsync(amount, prefer);
            var verdict = res.GetProperty("verdict").GetString() ?? "";
            VerdictText.Text = verdict switch
            {
                "safe" => "Yes — safe",
                "safe_via_other_method" => "Yes — use the other method",
                _ => "No — don't buy yet",
            };

            var rec = res.GetProperty("recommended");
            RecText.Text =
                $"Use: {UiCopy.PayMethod(JsonUi.Str(rec, "method"))} · {JsonUi.Str(rec, "account_name")}";
            ReasonText.Text = JsonUi.Str(rec, "reason");
            if (rec.TryGetProperty("remaining_after", out var rem) && rem.ValueKind != JsonValueKind.Null)
                ReasonText.Text += $"\nSafe to spend after: {JsonUi.Money(rec, "remaining_after")}";

            var opts = new List<string>();
            if (res.TryGetProperty("options", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in arr.EnumerateArray())
                {
                    var safe = o.TryGetProperty("safe", out var sf) && sf.GetBoolean();
                    opts.Add(
                        $"{(safe ? "✓" : "✗")} {UiCopy.PayMethod(JsonUi.Str(o, "method"))} · {JsonUi.Str(o, "account_name")} — " +
                        JsonUi.Str(o, "reason"));
                }
            }
            OptionsList.ItemsSource = opts.Count > 0 ? opts : new List<string> { "No alternate options." };
            ScopeText.Text =
                $"{UiCopy.MoneyView(AppState.IfppScope)}" +
                $" · as of {JsonUi.Str(res, "as_of")}";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Simulate_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.SimulateIfppAsync(new
            {
                extra_outflows = new[]
                {
                    new
                    {
                        amount,
                        name = "What-if purchase",
                        on_date = DateTime.Today.ToString("yyyy-MM-dd"),
                    },
                },
                profile_id = AppState.SelectedProfileId,
                scope = AppState.IfppScope,
            });
            SimText.Text = JsonUi.Str(res, "message");
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
