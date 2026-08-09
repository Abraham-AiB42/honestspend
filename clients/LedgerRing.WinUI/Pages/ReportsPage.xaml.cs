using System.Globalization;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class ReportsPage : Page
{
    public ReportsPage()
    {
        InitializeComponent();
        DaysBox.SelectionChanged += async (_, _) => await LoadAsync();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            var days = 30;
            if (DaysBox.SelectedItem is ComboBoxItem ci && ci.Tag is string t && int.TryParse(t, out var d))
                days = d;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var cf = await api.GetCashflowReportAsync(days);
            TitleText.Text = JsonUi.Str(cf, "title");
            if (cf.TryGetProperty("totals", out var tot))
            {
                TotalsText.Text =
                    $"In {Money(tot, "inflow")} · Out {Money(tot, "outflow")} · Net {Money(tot, "net")}";
            }

            var lines = new List<string>();
            if (cf.TryGetProperty("entities", out var ent) && ent.ValueKind == JsonValueKind.Array)
            {
                foreach (var e in ent.EnumerateArray())
                {
                    lines.Add(
                        $"{JsonUi.Str(e, "display_name")} ({UiCopy.EntityType(JsonUi.Str(e, "entity_type"))}) · " +
                        $"in {Money(e, "inflow")} · out {Money(e, "outflow")} · net {Money(e, "net")} · " +
                        $"cash {Money(e, "cash_balance")}");
                }
            }
            EntityList.ItemsSource = lines.Count > 0 ? lines : new List<string> { "No entities." };

            try
            {
                var fees = await api.GetFeeSummaryAsync(365);
                FeeText.Text =
                    $"{JsonUi.Str(fees, "count")} fee-like hits · total ${JsonUi.Str(fees, "total_abs")} · " +
                    $"~${JsonUi.Str(fees, "est_monthly_fee_drag")}/mo drag";
            }
            catch
            {
                FeeText.Text = "Fee summary unavailable.";
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static string Money(JsonElement el, string prop)
    {
        var s = JsonUi.Str(el, prop, "0");
        if (decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return d.ToString("C", CultureInfo.CurrentCulture);
        return s;
    }
}
