using System.Globalization;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class CreditPage : Page
{
    public CreditPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();
    private async void RunPlan_Click(object sender, RoutedEventArgs e) => await RunPlanAsync();
    private async void Compare_Click(object sender, RoutedEventArgs e) => await CompareAsync();

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            try
            {
                var status = await api.GetCreditStatusAsync();
                BureauNote.Text = JsonUi.Str(status, "message");
            }
            catch
            {
                BureauNote.Text = "No bureau/CK consumer API — educational model only.";
            }

            await LoadHistoryFormAsync(api);

            var health = await api.GetCreditHealthAsync();
            ScoreText.Text = JsonUi.Str(health, "score");
            BandText.Text = $"{JsonUi.Str(health, "band")} · {JsonUi.Str(health, "model")}";
            UtilText.Text =
                $"Util {JsonUi.Str(health, "utilization_overall_pct")}% · " +
                $"revolving {JsonUi.Money(health, "total_revolving_balance")} / {JsonUi.Money(health, "total_revolving_limit")} · " +
                $"available {JsonUi.Money(health, "available_credit")}";
            DisclaimerText.Text = JsonUi.Str(health, "disclaimer");

            if (health.TryGetProperty("your_reported_vantage", out var rv) && rv.ValueKind != JsonValueKind.Null)
            {
                BandText.Text +=
                    $" · your reported {rv.GetRawText()} · Δ {JsonUi.Str(health, "vs_reported_delta")}";
            }

            var factors = new List<string>();
            if (health.TryGetProperty("factors", out var fArr) && fArr.ValueKind == JsonValueKind.Array)
            {
                foreach (var f in fArr.EnumerateArray())
                {
                    var tips = "";
                    if (f.TryGetProperty("tips", out var t) && t.ValueKind == JsonValueKind.Array)
                    {
                        var tipList = t.EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrEmpty(x)).Take(2);
                        tips = string.Join(" · ", tipList!);
                    }
                    factors.Add(
                        $"{JsonUi.Str(f, "name")} ({JsonUi.Str(f, "weight_pct")}): " +
                        $"{JsonUi.Str(f, "score_0_100")}/100 · {JsonUi.Str(f, "detail")}" +
                        (string.IsNullOrEmpty(tips) ? "" : $"\n  Tips: {tips}"));
                }
            }
            FactorList.ItemsSource = factors.Count > 0 ? factors : new List<string> { "No factor breakdown." };

            var suggestions = new List<string>();
            if (health.TryGetProperty("suggestions", out var sug) && sug.ValueKind == JsonValueKind.Array)
            {
                foreach (var s in sug.EnumerateArray())
                    suggestions.Add("• " + (s.ValueKind == JsonValueKind.String ? s.GetString() : s.GetRawText()));
            }
            SuggestionList.ItemsSource = suggestions.Count > 0 ? suggestions : new List<string> { "No suggestions right now." };

            var whatIf = new List<string>();
            if (health.TryGetProperty("what_if", out var wi))
            {
                if (wi.ValueKind == JsonValueKind.Array)
                {
                    foreach (var w in wi.EnumerateArray())
                        whatIf.Add(FormatWhatIf(w));
                }
                else if (wi.ValueKind == JsonValueKind.Object)
                {
                    foreach (var p in wi.EnumerateObject())
                        whatIf.Add($"{p.Name}: {p.Value.GetRawText()}");
                }
            }
            WhatIfList.ItemsSource = whatIf.Count > 0 ? whatIf : new List<string> { "No what-if scenarios." };

            await RunPlanAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task LoadHistoryFormAsync(LedgerApiClient api)
    {
        try
        {
            var p = await api.GetCreditProfileAsync();
            OnTimeBox.Value = ParseD(p, "credit_on_time_rate", 1);
            Late30Box.Value = ParseD(p, "credit_late_30", 0);
            Late60Box.Value = ParseD(p, "credit_late_60", 0);
            Late90Box.Value = ParseD(p, "credit_late_90", 0);
            HardBox.Value = ParseD(p, "credit_hard_inquiries", 0);
            NewAcctBox.Value = ParseD(p, "credit_new_accounts", 0);
            if (p.TryGetProperty("credit_reported_vantage", out var rv) && rv.ValueKind != JsonValueKind.Null)
                ReportedBox.Value = ParseD(p, "credit_reported_vantage", double.NaN);
            else
                ReportedBox.Value = double.NaN;
            HistoryMsg.Text = JsonUi.Str(p, "disclaimer");
        }
        catch (Exception ex)
        {
            HistoryMsg.Text = "Could not load history: " + ex.Message;
        }
    }

    private async void SaveHistory_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var body = new Dictionary<string, object?>
            {
                ["credit_on_time_rate"] = double.IsNaN(OnTimeBox.Value) ? 1m : (decimal)OnTimeBox.Value,
                ["credit_late_30"] = IntBox(Late30Box),
                ["credit_late_60"] = IntBox(Late60Box),
                ["credit_late_90"] = IntBox(Late90Box),
                ["credit_hard_inquiries"] = IntBox(HardBox),
                ["credit_new_accounts"] = IntBox(NewAcctBox),
            };
            if (!double.IsNaN(ReportedBox.Value) && ReportedBox.Value >= 300)
                body["credit_reported_vantage"] = (int)ReportedBox.Value;
            else
                body["credit_reported_vantage"] = null;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PutCreditProfileAsync(body);
            HistoryMsg.Text =
                $"Saved · score {JsonUi.Str(res, "score")} · {JsonUi.Str(res, "band")}";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static int IntBox(NumberBox box)
        => double.IsNaN(box.Value) ? 0 : (int)box.Value;

    private static double ParseD(JsonElement s, string name, double fallback)
    {
        if (!s.TryGetProperty(name, out var el) || el.ValueKind == JsonValueKind.Null)
            return fallback;
        var raw = el.ValueKind == JsonValueKind.String ? el.GetString() : el.GetRawText();
        return double.TryParse(raw, NumberStyles.Any, CultureInfo.InvariantCulture, out var d) ? d : fallback;
    }

    private static string FormatWhatIf(JsonElement w)
    {
        if (w.ValueKind == JsonValueKind.String)
            return "• " + w.GetString();
        if (w.ValueKind == JsonValueKind.Object)
        {
            var title = JsonUi.Str(w, "title", JsonUi.Str(w, "scenario", "Scenario"));
            var score = JsonUi.Str(w, "score", JsonUi.Str(w, "new_score", ""));
            var detail = JsonUi.Str(w, "detail", JsonUi.Str(w, "description", ""));
            return string.IsNullOrEmpty(score)
                ? $"• {title}: {detail}"
                : $"• {title} → {score} · {detail}";
        }
        return "• " + w.GetRawText();
    }

    private async Task RunPlanAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            var strategy = "avalanche";
            if (StrategyBox.SelectedItem is ComboBoxItem si && si.Tag is string st)
                strategy = st;
            var extra = double.IsNaN(ExtraBox.Value) ? 0m : (decimal)ExtraBox.Value;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var plan = await api.GetDebtPlanAsync(strategy, extra);

            PlanSummary.Text =
                $"Debt {JsonUi.Money(plan, "total_balance")} · " +
                $"est. months {JsonUi.Str(plan, "estimated_months", "—")} · " +
                $"est. interest {JsonUi.Money(plan, "estimated_interest")}";

            OppBanner.Text = plan.TryGetProperty("opportunity_cost_aware", out var aw) && aw.GetBoolean()
                ? $"Hurdle {JsonUi.Str(plan, "opportunity_rate_pct")} · {JsonUi.Str(plan, "opportunity_rate_source")} · " +
                  $"extra parked in yield: {JsonUi.Money(plan, "extra_to_yield_not_debt")}/mo"
                : "Opportunity-cost off";

            var order = new List<string>();
            if (plan.TryGetProperty("order", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in arr.EnumerateArray())
                {
                    var rec = JsonUi.Str(o, "recommendation");
                    order.Add(
                        $"#{JsonUi.Str(o, "rank")} {JsonUi.Str(o, "name")} · {JsonUi.Money(o, "balance")} · " +
                        $"{JsonUi.Str(o, "effective_apr_pct")} · {rec.ToUpperInvariant()} · {JsonUi.Str(o, "reason")}");
                }
            }
            DebtOrder.ItemsSource = order;

            var iv = new List<string>();
            if (plan.TryGetProperty("invest_vs_debt", out var ivArr) && ivArr.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in ivArr.EnumerateArray())
                {
                    var edge = "—";
                    if (r.TryGetProperty("example_per_1000", out var ex) &&
                        ex.TryGetProperty("edge_of_keeping_cash", out var ed))
                        edge = ed.GetString() ?? "—";
                    iv.Add(
                        $"{JsonUi.Str(r, "name")}: debt {JsonUi.Str(r, "effective_apr_pct")} vs yield {JsonUi.Str(r, "opportunity_rate_pct")} · " +
                        $"{JsonUi.Str(r, "recommendation")} · $1k edge keep cash ${edge}");
                }
            }
            InvestVs.ItemsSource = iv.Count > 0 ? iv : new List<string> { "Set APY on savings/X Money for invest-vs-prepay." };
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task CompareAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            var extra = double.IsNaN(ExtraBox.Value) ? 0m : (decimal)ExtraBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var cmp = await api.GetDebtCompareAsync(extra);
            var lines = new List<string>();
            if (cmp.TryGetProperty("disclaimer", out var disc) && disc.ValueKind == JsonValueKind.String)
                lines.Add(disc.GetString() ?? "");
            if (cmp.TryGetProperty("comparisons", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var s in arr.EnumerateArray())
                {
                    lines.Add(
                        $"{JsonUi.Str(s, "strategy")}: " +
                        $"months {JsonUi.Str(s, "estimated_months")} · interest {JsonUi.Money(s, "estimated_interest")} · " +
                        $"extra→yield {JsonUi.Money(s, "extra_to_yield")}/mo · " +
                        $"first extra: {JsonUi.Str(s, "first_extra_target", "—")} ({JsonUi.Str(s, "first_reason", "")})");
                }
            }
            else if (cmp.TryGetProperty("strategies", out var arr2) && arr2.ValueKind == JsonValueKind.Array)
            {
                foreach (var s in arr2.EnumerateArray())
                {
                    lines.Add(
                        $"{JsonUi.Str(s, "strategy", JsonUi.Str(s, "name"))}: " +
                        $"months {JsonUi.Str(s, "estimated_months")} · interest {JsonUi.Money(s, "estimated_interest")}");
                }
            }
            CompareList.ItemsSource = lines.Count > 0 ? lines : new List<string> { "No compare data." };
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
