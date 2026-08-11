using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class TaxVaultPage : Page
{
    public TaxVaultPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var v = await api.GetTaxVaultAsync();
            EnabledBox.IsChecked = v.TryGetProperty("enabled", out var en) && en.GetBoolean();
            if (decimal.TryParse(JsonUi.Str(v, "balance", "0"), NumberStyles.Any, CultureInfo.InvariantCulture, out var bal))
                BalBox.Value = (double)bal;
            BalanceText.Text = JsonUi.Money(v, "balance");
            NoteText.Text = JsonUi.Str(v, "note");
            if (v.TryGetProperty("income_rate", out var ir) && ir.ValueKind != JsonValueKind.Null)
            {
                if (decimal.TryParse(ir.GetString() ?? ir.GetRawText(), NumberStyles.Any, CultureInfo.InvariantCulture, out var r))
                    RateBox.Value = (double)r;
            }

            var settings = await api.GetSettingsAsync();
            CliffOn.IsChecked = settings.TryGetProperty("income_cliff_enabled", out var c) && c.GetBoolean();
            if (settings.TryGetProperty("income_cliff_factor", out var f))
            {
                var fs = f.ValueKind == JsonValueKind.String ? f.GetString() : f.GetRawText();
                if (double.TryParse(fs, NumberStyles.Any, CultureInfo.InvariantCulture, out var fd))
                    CliffFactor.Value = fd;
            }
            MsgText.Text = "Loaded.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var body = new Dictionary<string, object?>
            {
                ["enabled"] = EnabledBox.IsChecked == true,
                ["balance"] = double.IsNaN(BalBox.Value) ? 0m : (decimal)BalBox.Value,
            };
            if (!double.IsNaN(RateBox.Value))
                body["income_rate"] = (decimal)RateBox.Value;
            else
                body["clear_income_rate"] = true;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var v = await api.PutTaxVaultAsync(body);
            BalanceText.Text = JsonUi.Money(v, "balance");
            MsgText.Text = "Vault saved — Safe to spend reduced by reserve.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Delta_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var delta = double.IsNaN(DeltaBox.Value) ? 0m : (decimal)DeltaBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var v = await api.AdjustTaxVaultAsync(delta);
            if (decimal.TryParse(JsonUi.Str(v, "balance", "0"), NumberStyles.Any, CultureInfo.InvariantCulture, out var bal))
                BalBox.Value = (double)bal;
            BalanceText.Text = JsonUi.Money(v, "balance");
            MsgText.Text = $"Adjusted by {delta}.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Cliff_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var cur = await api.GetSettingsAsync();
            // merge via dictionary from raw — simpler: send known fields
            var dict = new Dictionary<string, object?>();
            foreach (var p in cur.EnumerateObject())
            {
                dict[p.Name] = p.Value.ValueKind switch
                {
                    JsonValueKind.String => p.Value.GetString(),
                    JsonValueKind.Number => p.Value.GetDouble(),
                    JsonValueKind.True => true,
                    JsonValueKind.False => false,
                    JsonValueKind.Null => null,
                    _ => p.Value.GetRawText(),
                };
            }
            dict["income_cliff_enabled"] = CliffOn.IsChecked == true;
            dict["income_cliff_factor"] = double.IsNaN(CliffFactor.Value) ? 1m : (decimal)CliffFactor.Value;
            await api.PutSettingsAsync(dict);
            MsgText.Text = "Income cliffs saved.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
