using System.Globalization;
using System.Text;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace LedgerRing_WinUI.Pages;

public sealed partial class ReportsPage : Page
{
    private JsonElement _lastCashflow;

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
            _lastCashflow = cf.Clone();
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

            try
            {
                var debt = await api.GetDebtReportAsync();
                DebtTotals.Text =
                    $"Total {Money(debt, "total_balance")} · {JsonUi.Str(debt, "count")} accounts · " +
                    $"est. months {JsonUi.Str(debt, "estimated_months", "—")}";
                var dLines = new List<string>();
                if (debt.TryGetProperty("debts", out var da) && da.ValueKind == JsonValueKind.Array)
                {
                    foreach (var d in da.EnumerateArray())
                        dLines.Add(
                            $"{JsonUi.Str(d, "name")} · {Money(d, "balance")} · {JsonUi.Str(d, "apr_pct")} · " +
                            JsonUi.Str(d, "recommendation"));
                }
                DebtList.ItemsSource = dLines.Count > 0 ? dLines : new List<string> { "No debts." };
            }
            catch
            {
                DebtTotals.Text = "Debt snapshot unavailable.";
                DebtList.ItemsSource = new List<string>();
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Export_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (_lastCashflow.ValueKind != JsonValueKind.Object)
            {
                await LoadAsync();
                if (_lastCashflow.ValueKind != JsonValueKind.Object)
                    throw new InvalidOperationException("Load a report first.");
            }
            var sb = new StringBuilder();
            sb.AppendLine("entity,entity_type,inflow,outflow,net,cash_balance");
            if (_lastCashflow.TryGetProperty("entities", out var ent) && ent.ValueKind == JsonValueKind.Array)
            {
                foreach (var row in ent.EnumerateArray())
                {
                    sb.Append(Csv(JsonUi.Str(row, "display_name"))).Append(',');
                    sb.Append(Csv(JsonUi.Str(row, "entity_type"))).Append(',');
                    sb.Append(JsonUi.Str(row, "inflow", "0")).Append(',');
                    sb.Append(JsonUi.Str(row, "outflow", "0")).Append(',');
                    sb.Append(JsonUi.Str(row, "net", "0")).Append(',');
                    sb.Append(JsonUi.Str(row, "cash_balance", "0")).AppendLine();
                }
            }
            var picker = new FileSavePicker();
            var hwnd = WindowNative.GetWindowHandle(App.MainWindowInstance);
            InitializeWithWindow.Initialize(picker, hwnd);
            picker.SuggestedFileName = "ledgerring-cashflow.csv";
            picker.FileTypeChoices.Add("CSV", new List<string> { ".csv" });
            var file = await picker.PickSaveFileAsync();
            if (file is null) return;
            await File.WriteAllTextAsync(file.Path, sb.ToString(), Encoding.UTF8);
            TotalsText.Text += " · Exported CSV.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static string Csv(string s)
    {
        if (s.Contains('"') || s.Contains(',') || s.Contains('\n'))
            return "\"" + s.Replace("\"", "\"\"") + "\"";
        return s;
    }

    private static string Money(JsonElement el, string prop)
    {
        var s = JsonUi.Str(el, prop, "0");
        if (decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return d.ToString("C", CultureInfo.CurrentCulture);
        return s;
    }
}
