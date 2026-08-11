using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class CreditPage : Page
{
    private readonly Dictionary<int, string> _cashNames = new();
    private bool _suppressCycleCardChange;
    private bool _suppressPromoPickChange;

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
            await LoadCycleSectionAsync(api);

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
                        $"{JsonUi.Str(o, "rank")}. {JsonUi.Str(o, "name")} · {JsonUi.Money(o, "balance")} · " +
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
            InvestVs.ItemsSource = iv.Count > 0 ? iv : new List<string> { "Set APY on savings/HYSA for invest-vs-prepay." };
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

    private async Task LoadCycleSectionAsync(LedgerApiClient api)
    {
        try
        {
            _cashNames.Clear();
            CycleFundingBox.Items.Clear();
            CycleFundingBox.Items.Add(new ComboBoxItem { Content = "(no cash selected)", Tag = 0 });

            var accounts = await api.GetAccountsAsync();
            if (accounts.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in accounts.EnumerateArray())
                {
                    var kind = JsonUi.Str(a, "kind").ToLowerInvariant();
                    var isCash = a.TryGetProperty("is_cash_for_ifpp", out var f) && f.ValueKind == JsonValueKind.True;
                    if (kind is "checking" or "savings" or "cash" || isCash)
                    {
                        var id = a.GetProperty("id").GetInt32();
                        var name = JsonUi.Str(a, "nickname");
                        _cashNames[id] = name;
                        CycleFundingBox.Items.Add(new ComboBoxItem
                        {
                            Content = $"{name} · {UiCopy.AccountKind(kind)} · {JsonUi.Money(a, "current_balance")}",
                            Tag = id,
                        });
                    }
                }
            }
            if (CycleFundingBox.Items.Count > 0)
                CycleFundingBox.SelectedIndex = 0;

            var cycles = await api.GetAccountCyclesAsync();
            var summary = new List<string>();
            var prevId = SelectedCycleCardId();

            _suppressCycleCardChange = true;
            CycleCardBox.Items.Clear();
            if (cycles.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var it in arr.EnumerateArray())
                {
                    var id = JsonUi.Int(it, "account_id");
                    var name = JsonUi.Str(it, "name");
                    var policy = UiCopy.AutopayPolicy(JsonUi.Str(it, "policy", JsonUi.Str(it, "autopay_policy")));
                    var fundId = JsonUi.Int(it, "funding_account_id",
                        JsonUi.Int(it, "payment_funding_account_id"));
                    var fund = fundId > 0 && _cashNames.TryGetValue(fundId, out var fn) ? fn : "—";
                    summary.Add(
                        $"{name} · close {JsonUi.Str(it, "statement_close_day", "?")} · " +
                        $"due {JsonUi.Str(it, "payment_due_day", "?")} · {policy} · " +
                        $"from {fund} · statement {JsonUi.Money(it, "statement_balance")} · " +
                        $"next {JsonUi.Money(it, "next_payment")} on {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(it, "next_due"))}");
                    CycleCardBox.Items.Add(new ComboBoxItem { Content = name, Tag = id });
                }
            }
            CycleSummaryList.ItemsSource = summary.Count > 0
                ? summary
                : new List<string> { "No credit cards yet — Add → Credit card from Home." };

            if (CycleCardBox.Items.Count > 0)
            {
                var sel = 0;
                if (prevId is int keep)
                {
                    for (var i = 0; i < CycleCardBox.Items.Count; i++)
                    {
                        if (CycleCardBox.Items[i] is ComboBoxItem { Tag: int tid } && tid == keep)
                        {
                            sel = i;
                            break;
                        }
                    }
                }
                CycleCardBox.SelectedIndex = sel;
            }
            _suppressCycleCardChange = false;

            await ApplySelectedCycleAsync(api);
        }
        catch (Exception ex)
        {
            CycleSummaryList.ItemsSource = new List<string> { "Could not load statement cycles: " + ex.Message };
            PromoLineList.ItemsSource = new List<string> { "—" };
            PromoEditPickBox.Items.Clear();
        }
    }

    private int? SelectedCycleCardId()
    {
        if (CycleCardBox.SelectedItem is ComboBoxItem { Tag: int id } && id > 0)
            return id;
        return null;
    }

    private async void CycleCard_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressCycleCardChange) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await ApplySelectedCycleAsync(api);
        }
        catch (Exception ex)
        {
            CycleMsg.Text = ex.Message;
        }
    }

    private async Task ApplySelectedCycleAsync(LedgerApiClient api)
    {
        if (SelectedCycleCardId() is not int id)
        {
            CycleProjectedText.Text = "Projected statement: —";
            CycleNextPaymentText.Text = "Next payment: —";
            CyclePeakText.Text = "";
            CyclePeakText.Visibility = Visibility.Collapsed;
            CycleDatesText.Text = "";
            PromoLineList.ItemsSource = new List<string> { "Pick a card to see promo lines." };
            PromoCalendarSummary.Text = "Pick a card to see the promo calendar.";
            PromoCalendarList.ItemsSource = new List<string>();
            return;
        }

        try
        {
            var c = await api.GetAccountCycleAsync(id);
            ApplyCycleProjection(c);

            CycleCloseDayBox.Value = ParseD(c, "statement_close_day", 1);
            CycleDueDayBox.Value = ParseD(c, "payment_due_day", 15);
            SelectPolicyTag(CyclePolicyBox, JsonUi.Str(c, "policy", JsonUi.Str(c, "autopay_policy", "statement")));
            SelectPolicyTag(CycleTimingBox, JsonUi.Str(c, "payment_timing", "on_due"));
            var fixedAmt = ParseD(c, "payment_fixed_amount", double.NaN);
            CycleFixedBox.Value = double.IsNaN(fixedAmt) ? 0 : fixedAmt;

            var fundId = JsonUi.Int(c, "funding_account_id", JsonUi.Int(c, "payment_funding_account_id"));
            SelectIntTag(CycleFundingBox, fundId);

            await LoadPeakLineAsync(api, id);
            await LoadPromoLinesAsync(api, id);
            await LoadFreezeHistoryAsync(api, id);
        }
        catch (Exception ex)
        {
            CycleMsg.Text = "Could not load card cycle: " + ex.Message;
        }
    }

    private async Task LoadPeakLineAsync(LedgerApiClient api, int accountId)
    {
        try
        {
            var peak = await api.GetAccountPeakAsync(accountId, 90);
            var days = JsonUi.Int(peak, "lookback_days", 90);
            var lookback = JsonUi.Money(peak, "peak_lookback");
            var cycle = JsonUi.Money(peak, "peak_open_cycle");
            CyclePeakText.Text = $"Peak last {days}d: {lookback} · this cycle: {cycle}";
            CyclePeakText.Visibility = Visibility.Visible;
        }
        catch
        {
            CyclePeakText.Text = "";
            CyclePeakText.Visibility = Visibility.Collapsed;
        }
    }

    private async Task LoadFreezeHistoryAsync(LedgerApiClient api, int accountId)
    {
        try
        {
            var hist = await api.GetStatementCyclesAsync(accountId);
            var rows = new List<string>();
            if (hist.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var it in arr.EnumerateArray())
                {
                    var end = JsonUi.Str(it, "cycle_end");
                    var act = JsonUi.Money(it, "actual_balance");
                    var proj = JsonUi.Money(it, "projected_balance");
                    var var = JsonUi.Money(it, "variance");
                    rows.Add($"Close {end} · actual {act} · projected {proj} · variance {var}");
                }
            }
            FreezeHistoryList.ItemsSource = rows.Count > 0
                ? rows
                : new List<string> { "No frozen statements yet." };
        }
        catch
        {
            FreezeHistoryList.ItemsSource = new List<string> { "Statement history unavailable." };
        }
    }

    private async void FreezeStatement_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int id)
                throw new InvalidOperationException("Pick a card.");
            var amt = double.IsNaN(FreezeActualBox.Value) ? 0m : (decimal)FreezeActualBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.FreezeStatementCycleAsync(id, new
            {
                actual_balance = amt,
                source = "user",
            });
            FreezeMsg.Text =
                $"Frozen close {JsonUi.Str(res, "cycle_end")} · actual {JsonUi.Money(res, "actual_balance")} · " +
                $"projected {JsonUi.Money(res, "projected_balance")} · variance {JsonUi.Money(res, "variance")}";
            await LoadFreezeHistoryAsync(api, id);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void RewardsPick_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var cat = "general";
            if (RewardsCategoryBox.SelectedItem is ComboBoxItem { Tag: string t })
                cat = t;
            var amt = double.IsNaN(RewardsAmountBox.Value) ? 0m : (decimal)RewardsAmountBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PickRewardsCardAsync(cat, amt > 0 ? amt : null);
            if (res.TryGetProperty("best", out var best) && best.ValueKind == JsonValueKind.Object)
            {
                RewardsPickMsg.Text =
                    $"Best for {JsonUi.Str(res, "category")}: {JsonUi.Str(best, "name")} · " +
                    $"{JsonUi.Str(best, "rate_percent")}% rewards" +
                    (best.TryGetProperty("fits_amount", out var fit) && fit.ValueKind == JsonValueKind.False
                        ? " · may not fit amount (limit)"
                        : "");
            }
            else
            {
                RewardsPickMsg.Text = "No credit cards ranked.";
            }
            var list = new List<string>();
            if (res.TryGetProperty("cards", out var cards) && cards.ValueKind == JsonValueKind.Array)
            {
                foreach (var c in cards.EnumerateArray())
                {
                    list.Add(
                        $"{JsonUi.Str(c, "name")} · {JsonUi.Str(c, "rate_percent")}% · " +
                        $"avail {JsonUi.Money(c, "available_credit")} · util {JsonUi.Str(c, "utilization_pct")}%");
                }
            }
            RewardsPickList.ItemsSource = list;
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void ApplyCycleProjection(JsonElement c)
    {
        CycleProjectedText.Text = $"Projected statement: {JsonUi.Money(c, "statement_balance")}";
        CycleNextPaymentText.Text =
            $"Next payment: {JsonUi.Money(c, "next_payment")} on {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(c, "next_due"))}";
        var timing = JsonUi.Str(c, "payment_timing", "on_due");
        CycleDatesText.Text =
            $"Last close {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(c, "last_close"))} · next close {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(c, "next_close"))} · " +
            $"{UiCopy.AutopayPolicy(JsonUi.Str(c, "policy", JsonUi.Str(c, "autopay_policy")))} · " +
            $"{UiCopy.PaymentTiming(timing)}";

        // Warnings
        var warnParts = new List<string>();
        if (c.TryGetProperty("warnings", out var warns) && warns.ValueKind == JsonValueKind.Array)
        {
            foreach (var w in warns.EnumerateArray())
            {
                var s = w.GetString();
                if (!string.IsNullOrWhiteSpace(s))
                    warnParts.Add(s!);
            }
        }
        if (warnParts.Count > 0)
        {
            CycleWarningsText.Text = string.Join("\n", warnParts);
            CycleWarningsText.Visibility = Visibility.Visible;
        }
        else
        {
            CycleWarningsText.Text = "";
            CycleWarningsText.Visibility = Visibility.Collapsed;
        }

        ApplyHonestyPanel(c);
    }

    private void ApplyHonestyPanel(JsonElement c)
    {
        if (!c.TryGetProperty("honesty", out var h) || h.ValueKind != JsonValueKind.Object)
        {
            CycleHonestyMinText.Text = "Min payment: —";
            CycleHonestyInterestText.Text = "Est. monthly interest if min only: —";
            CycleHonestyUtilText.Text = "Utilization: —";
            CycleAdviceList.ItemsSource = new List<string>();
            return;
        }

        var minSrc = JsonUi.Str(h, "min_payment_source", "estimated");
        CycleHonestyMinText.Text =
            $"Min payment: {JsonUi.Money(h, "min_payment")} ({minSrc})";
        CycleHonestyInterestText.Text =
            $"Est. monthly interest if min only: {JsonUi.Money(h, "estimated_monthly_interest_if_min")}" +
            (string.IsNullOrEmpty(JsonUi.Str(h, "apr", "")) || JsonUi.Str(h, "apr") == "—"
                ? " · set APR on the account for a better estimate"
                : $" · APR {JsonUi.Str(h, "apr")}");
        var util = JsonUi.Str(h, "utilization_pct", "");
        CycleHonestyUtilText.Text = string.IsNullOrEmpty(util) || util == "—"
            ? "Utilization: — (set credit limit)"
            : $"Utilization: {util}% · pay {JsonUi.Money(h, "amount_to_util_hard")} to hit {JsonUi.Str(h, "util_hard_pct", "30")}% · " +
              $"pay {JsonUi.Money(h, "amount_to_util_soft")} to hit {JsonUi.Str(h, "util_soft_pct", "10")}%";
        CycleHonestyDisclaimer.Text = JsonUi.Str(h, "disclaimer",
            "Educational estimates only. Not a credit score.");

        var advice = new List<string>();
        if (h.TryGetProperty("advice", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var a in arr.EnumerateArray())
            {
                var s = a.GetString();
                if (!string.IsNullOrWhiteSpace(s))
                    advice.Add("• " + s);
            }
        }
        CycleAdviceList.ItemsSource = advice;
    }

    private async Task LoadPromoLinesAsync(LedgerApiClient api, int accountId)
    {
        try
        {
            var res = await api.GetPromoLinesAsync(accountId);
            var lines = new List<string>();
            _suppressPromoPickChange = true;
            try
            {
                PromoEditPickBox.Items.Clear();
                PromoEditPickBox.Items.Add(new ComboBoxItem
                {
                    Content = "(new plan — not editing)",
                    Tag = (PromoPick?)null,
                });
                if (res.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
                {
                    foreach (var ln in arr.EnumerateArray())
                    {
                        var active = !ln.TryGetProperty("active", out var act) || act.ValueKind != JsonValueKind.False;
                        var status = active ? "open" : "closed";
                        var name = JsonUi.Str(ln, "name");
                        lines.Add(
                            $"{name} · remaining {JsonUi.Money(ln, "principal_remaining")} · " +
                            $"monthly {JsonUi.Money(ln, "monthly_payment")} · {status}" +
                            (string.IsNullOrEmpty(JsonUi.Str(ln, "end_date", "")) || JsonUi.Str(ln, "end_date") == "—"
                                ? ""
                                : $" · ends {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(ln, "end_date"))}"));
                        var lineId = ln.TryGetProperty("id", out var idEl) && idEl.TryGetInt32(out var lid) ? lid : 0;
                        if (lineId > 0)
                        {
                            var rem = ParseD(ln, "principal_remaining", 0);
                            var mon = ParseD(ln, "monthly_payment", 0);
                            var pick = new PromoPick(lineId, name, rem, mon);
                            PromoEditPickBox.Items.Add(new ComboBoxItem
                            {
                                Content = $"{name} · rem {JsonUi.Money(ln, "principal_remaining")}",
                                Tag = pick,
                            });
                        }
                    }
                }
                PromoEditPickBox.SelectedIndex = 0;
            }
            finally
            {
                _suppressPromoPickChange = false;
            }
            PromoLineList.ItemsSource = lines.Count > 0
                ? lines
                : new List<string> { "No promo/installment lines on this card." };

            await LoadPromoCalendarAsync(api, accountId);
        }
        catch
        {
            PromoLineList.ItemsSource = new List<string> { "Promo lines unavailable." };
            PromoEditPickBox.Items.Clear();
            PromoCalendarSummary.Text = "Promo calendar unavailable.";
            PromoCalendarList.ItemsSource = new List<string>();
        }
    }

    private async Task LoadPromoCalendarAsync(LedgerApiClient api, int accountId)
    {
        try
        {
            var cal = await api.GetPromoCalendarAsync(accountId);
            var span = JsonUi.Int(cal, "months_span", 0);
            var lineCount = JsonUi.Int(cal, "line_count", 0);
            if (lineCount == 0 || span == 0)
            {
                PromoCalendarSummary.Text = "No open promo plans to project.";
                PromoCalendarList.ItemsSource = new List<string>();
                return;
            }

            var lineBits = new List<string>();
            if (cal.TryGetProperty("lines", out var linesEl) && linesEl.ValueKind == JsonValueKind.Array)
            {
                foreach (var ln in linesEl.EnumerateArray())
                {
                    var name = JsonUi.Str(ln, "name");
                    var mo = JsonUi.Int(ln, "months_remaining", 0);
                    var fin = JsonUi.Str(ln, "final_month", "");
                    lineBits.Add($"{name}: {mo} mo" + (string.IsNullOrEmpty(fin) || fin == "—" ? "" : $" → {fin}"));
                }
            }

            PromoCalendarSummary.Text =
                $"{lineCount} plan(s) · longest run {span} months" +
                (lineBits.Count > 0 ? " · " + string.Join(" · ", lineBits) : "") +
                (cal.TryGetProperty("capped", out var cap) && cap.ValueKind == JsonValueKind.True
                    ? " · (hit safety cap — check monthly payment)"
                    : "");

            var rows = new List<string>();
            if (cal.TryGetProperty("by_month", out var byMo) && byMo.ValueKind == JsonValueKind.Array)
            {
                foreach (var m in byMo.EnumerateArray())
                {
                    var month = JsonUi.Str(m, "month");
                    var pay = JsonUi.Money(m, "payment_total");
                    var after = JsonUi.Money(m, "principal_after_total");
                    var n = JsonUi.Int(m, "line_count", 1);
                    var names = new List<string>();
                    if (m.TryGetProperty("lines", out var mls) && mls.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var ml in mls.EnumerateArray())
                            names.Add(JsonUi.Str(ml, "name"));
                    }
                    var label = names.Count > 0 ? string.Join(", ", names) : $"{n} plan(s)";
                    rows.Add($"{month}  ·  pay {pay}  ·  remaining after {after}  ·  {label}");
                }
            }
            PromoCalendarList.ItemsSource = rows;
        }
        catch
        {
            PromoCalendarSummary.Text = "Promo calendar unavailable.";
            PromoCalendarList.ItemsSource = new List<string>();
        }
    }

    private void PromoEditPick_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressPromoPickChange) return;
        if (PromoEditPickBox.SelectedItem is not ComboBoxItem { Tag: PromoPick pick })
            return;
        PromoNameBox.Text = pick.Name;
        PromoRemainingBox.Value = double.IsNaN(pick.Remaining) ? 0 : pick.Remaining;
        PromoMonthlyBox.Value = double.IsNaN(pick.Monthly) ? 0 : pick.Monthly;
    }

    private int? SelectedPromoLineId()
    {
        if (PromoEditPickBox.SelectedItem is ComboBoxItem { Tag: PromoPick pick })
            return pick.Id;
        return null;
    }

    private sealed record PromoPick(int Id, string Name, double Remaining, double Monthly);

    private async void CycleSave_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int id)
                throw new InvalidOperationException("Pick a card.");

            var policy = "statement";
            if (CyclePolicyBox.SelectedItem is ComboBoxItem { Tag: string p })
                policy = p;
            var timing = "on_due";
            if (CycleTimingBox.SelectedItem is ComboBoxItem { Tag: string t })
                timing = t;

            var body = new Dictionary<string, object?>
            {
                ["statement_close_day"] = double.IsNaN(CycleCloseDayBox.Value) ? 1 : (int)CycleCloseDayBox.Value,
                ["payment_due_day"] = double.IsNaN(CycleDueDayBox.Value) ? 15 : (int)CycleDueDayBox.Value,
                ["autopay_policy"] = policy,
                ["payment_timing"] = timing,
            };

            if (CycleFundingBox.SelectedItem is ComboBoxItem { Tag: int fundId } && fundId > 0)
                body["payment_funding_account_id"] = fundId;
            else
                body["payment_funding_account_id"] = null;

            if (policy == "fixed")
            {
                var amt = double.IsNaN(CycleFixedBox.Value) ? 0m : (decimal)CycleFixedBox.Value;
                body["payment_fixed_amount"] = amt;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PutAccountCycleConfigAsync(id, body);
            ApplyCycleProjection(res);
            CycleMsg.Text =
                $"Saved · next {JsonUi.Money(res, "next_payment")} on {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(res, "next_due"))} · " +
                $"settings locked as yours";
            await LoadCycleSectionAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void CyclePayToHard_Click(object sender, RoutedEventArgs e) =>
        await ApplyUtilTargetAsync(hard: true);

    private async void CyclePayToSoft_Click(object sender, RoutedEventArgs e) =>
        await ApplyUtilTargetAsync(hard: false);

    private async Task ApplyUtilTargetAsync(bool hard)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int id)
                throw new InvalidOperationException("Pick a card.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var c = await api.GetAccountCycleAsync(id);
            if (!c.TryGetProperty("honesty", out var h) || h.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException("Honesty data unavailable.");
            var key = hard ? "amount_to_util_hard" : "amount_to_util_soft";
            var amtStr = JsonUi.Str(h, key, "0");
            if (!decimal.TryParse(amtStr, System.Globalization.NumberStyles.Any,
                    System.Globalization.CultureInfo.InvariantCulture, out var amt))
                amt = 0m;
            if (amt <= 0)
            {
                CycleMsg.Text = hard
                    ? "Already at or under the 30% utilization target."
                    : "Already at or under the 10% utilization target.";
                return;
            }

            SelectPolicyTag(CyclePolicyBox, "fixed");
            CycleFixedBox.Value = (double)amt;
            var timing = "on_due";
            if (CycleTimingBox.SelectedItem is ComboBoxItem { Tag: string t })
                timing = t;
            var body = new Dictionary<string, object?>
            {
                ["autopay_policy"] = "fixed",
                ["payment_fixed_amount"] = amt,
                ["payment_timing"] = timing,
                ["statement_close_day"] = double.IsNaN(CycleCloseDayBox.Value) ? 1 : (int)CycleCloseDayBox.Value,
                ["payment_due_day"] = double.IsNaN(CycleDueDayBox.Value) ? 15 : (int)CycleDueDayBox.Value,
            };
            if (CycleFundingBox.SelectedItem is ComboBoxItem { Tag: int fundId } && fundId > 0)
                body["payment_funding_account_id"] = fundId;
            var res = await api.PutAccountCycleConfigAsync(id, body);
            ApplyCycleProjection(res);
            CycleMsg.Text =
                $"Set fixed pay {JsonUi.Money(res, "next_payment")} to aim for " +
                (hard ? "30%" : "10%") + " utilization · save already applied";
            await LoadCycleSectionAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void CycleRecompute_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int id)
                throw new InvalidOperationException("Pick a card.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.RecomputeAccountCycleAsync(id);
            ApplyCycleProjection(res);
            CycleMsg.Text =
                $"Recomputed · statement {JsonUi.Money(res, "statement_balance")} · " +
                $"next {JsonUi.Money(res, "next_payment")} on {PlainDateUi.FormatPlainWeekdayDate(JsonUi.Str(res, "next_due"))}";
            await LoadCycleSectionAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void PromoAdd_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int id)
                throw new InvalidOperationException("Pick a card first.");
            var name = PromoNameBox.Text?.Trim();
            if (string.IsNullOrEmpty(name))
                throw new InvalidOperationException("Enter a plan name.");
            var remaining = double.IsNaN(PromoRemainingBox.Value) ? 0m : (decimal)PromoRemainingBox.Value;
            var monthly = double.IsNaN(PromoMonthlyBox.Value) ? 0m : (decimal)PromoMonthlyBox.Value;
            if (remaining < 0 || monthly < 0)
                throw new InvalidOperationException("Amounts must be zero or more.");

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreatePromoLineAsync(id, new
            {
                name,
                principal_remaining = remaining,
                monthly_payment = monthly,
                start_date = DateTime.Today.ToString("yyyy-MM-dd"),
                source = "user",
                active = true,
            });
            PromoMsg.Text = $"Added “{name}” · remaining {remaining:C} · monthly {monthly:C}";
            PromoNameBox.Text = "";
            PromoRemainingBox.Value = 0;
            PromoMonthlyBox.Value = 0;
            await ApplySelectedCycleAsync(api);
            await LoadCycleSectionAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void PromoSave_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int cardId)
                throw new InvalidOperationException("Pick a card first.");
            if (SelectedPromoLineId() is not int lineId)
                throw new InvalidOperationException("Pick a plan to edit (or use Add for a new plan).");
            var remaining = double.IsNaN(PromoRemainingBox.Value) ? 0m : (decimal)PromoRemainingBox.Value;
            var monthly = double.IsNaN(PromoMonthlyBox.Value) ? 0m : (decimal)PromoMonthlyBox.Value;
            if (remaining < 0 || monthly < 0)
                throw new InvalidOperationException("Amounts must be zero or more.");

            var body = new Dictionary<string, object?>
            {
                ["principal_remaining"] = remaining,
                ["monthly_payment"] = monthly,
            };
            var name = PromoNameBox.Text?.Trim();
            if (!string.IsNullOrEmpty(name))
                body["name"] = name;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PatchPromoLineAsync(cardId, lineId, body);
            PromoMsg.Text =
                $"Updated “{JsonUi.Str(res, "name")}” · remaining {JsonUi.Money(res, "principal_remaining")} · " +
                $"monthly {JsonUi.Money(res, "monthly_payment")}";
            await ApplySelectedCycleAsync(api);
            await LoadCycleSectionAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void PromoRoll_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedCycleCardId() is not int cardId)
                throw new InvalidOperationException("Pick a card first.");
            if (SelectedPromoLineId() is not int lineId)
                throw new InvalidOperationException("Pick a plan to roll one month.");

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.RollPromoLineAsync(cardId, lineId);
            PromoMsg.Text =
                $"Rolled “{JsonUi.Str(res, "name")}” · remaining now {JsonUi.Money(res, "principal_remaining")} " +
                $"(reduced by monthly payment)";
            await ApplySelectedCycleAsync(api);
            await LoadCycleSectionAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static void SelectPolicyTag(ComboBox box, string policy)
    {
        var want = (policy ?? "statement").ToLowerInvariant();
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem { Tag: string t } &&
                string.Equals(t, want, StringComparison.OrdinalIgnoreCase))
            {
                box.SelectedIndex = i;
                return;
            }
        }
        // default statement
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem { Tag: string t } && t == "statement")
            {
                box.SelectedIndex = i;
                return;
            }
        }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
    }

    private static void SelectIntTag(ComboBox box, int id)
    {
        if (id <= 0)
        {
            if (box.Items.Count > 0) box.SelectedIndex = 0;
            return;
        }
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem { Tag: int t } && t == id)
            {
                box.SelectedIndex = i;
                return;
            }
        }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
    }

}

