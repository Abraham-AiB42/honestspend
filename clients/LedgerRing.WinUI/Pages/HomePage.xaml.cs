using System.Globalization;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;

namespace LedgerRing_WinUI.Pages;

public sealed partial class HomePage : Page
{
    private bool _loadingUi;
    private bool _dailyMode = true;

    public HomePage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        AppState.ScopeChanged += OnAppScopeChanged;
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            if (ls["HomeViewMode"] is string m && m == "full")
            {
                _dailyMode = false;
                ViewModeBox.SelectedIndex = 1;
            }
        }
        catch { /* ignore */ }
        ApplyViewMode();
        await RefreshAsync();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        base.OnNavigatedFrom(e);
        AppState.ScopeChanged -= OnAppScopeChanged;
    }

    private async void OnAppScopeChanged() => await RefreshAsync();

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private void ViewMode_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_loadingUi) return;
        if (ViewModeBox.SelectedItem is ComboBoxItem vi && vi.Tag is string t)
            _dailyMode = t == "daily";
        try
        {
            ApplicationData.Current.LocalSettings.Values["HomeViewMode"] = _dailyMode ? "daily" : "full";
        }
        catch { /* ignore */ }
        ApplyViewMode();
    }

    private void Expand_Click(object sender, RoutedEventArgs e)
    {
        _dailyMode = false;
        _loadingUi = true;
        ViewModeBox.SelectedIndex = 1;
        _loadingUi = false;
        ApplyViewMode();
    }

    private void Done_Click(object sender, RoutedEventArgs e)
    {
        DoneText.Text = $"Done · {DateTime.Now:t} — open rarely; engine + tray watch red days.";
    }

    private async void Rescue_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        RescueText.Text = "Analyzing…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            // Prefer covering a shortfall vs current cash spendable gap of $500 default planning amount
            var res = await api.LiquidityRescueAsync(amount: 500m);
            RescueText.Text = JsonUi.Str(res, "message");
            var lines = new List<string>();
            if (res.TryGetProperty("options", out var opts) && opts.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in opts.EnumerateArray())
                {
                    var safe = o.TryGetProperty("safe", out var s) && s.ValueKind == JsonValueKind.True ? "✓" : "!";
                    lines.Add(
                        $"#{JsonUi.Str(o, "rank")} {safe} {JsonUi.Str(o, "title")} · " +
                        $"cost {JsonUi.Str(o, "cost_estimate")} {JsonUi.Str(o, "cost_unit")} · " +
                        JsonUi.Str(o, "reason"));
                }
            }
            if (lines.Count == 0) lines.Add("No options returned.");
            RescueList.ItemsSource = lines;
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            RescueText.Text = "";
        }
    }

    private async void Brief_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        BriefText.Text = "Writing brief…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.GetDigestBriefAsync(useGrok: true);
            BriefText.Text =
                $"[{JsonUi.Str(res, "source")}] {JsonUi.Str(res, "brief")}";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            BriefText.Text = "";
        }
    }

    private async void FeeSummary_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.GetFeeSummaryAsync(365);
            BriefText.Text = JsonUi.Str(res, "message");
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void ApplyViewMode()
    {
        DailyPanel.Visibility = _dailyMode ? Visibility.Visible : Visibility.Collapsed;
        FullPanel.Visibility = _dailyMode ? Visibility.Collapsed : Visibility.Visible;
    }

    private async void Scope_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_loadingUi) return;
        ApplyScopeFromUi();
        await RefreshAsync();
    }

    private void ApplyScopeFromUi()
    {
        if (ScopeBox.SelectedItem is ComboBoxItem sc && sc.Tag is string st)
            AppState.IfppScope = st;
        if (AppState.IfppScope == "entity" && EntityBox.SelectedItem is ComboBoxItem ei && ei.Tag is int id)
            AppState.SelectedProfileId = id;
        else if (AppState.IfppScope == "group")
            AppState.SelectedProfileId = null;
        EntityBox.IsEnabled = AppState.IfppScope == "entity";
    }

    private async Task LoadEntitiesAsync(LedgerApiClient api)
    {
        _loadingUi = true;
        try
        {
            var profiles = await api.GetProfilesAsync();
            EntityBox.Items.Clear();
            var idx = 0;
            var i = 0;
            foreach (var p in profiles.EnumerateArray())
            {
                var id = p.GetProperty("id").GetInt32();
                EntityBox.Items.Add(new ComboBoxItem
                {
                    Content = JsonUi.Str(p, "display_name"),
                    Tag = id,
                });
                if (AppState.SelectedProfileId == id) idx = i;
                i++;
            }
            if (EntityBox.Items.Count > 0)
                EntityBox.SelectedIndex = AppState.SelectedProfileId is null ? 0 : idx;

            for (var j = 0; j < ScopeBox.Items.Count; j++)
            {
                if (ScopeBox.Items[j] is ComboBoxItem cbi && cbi.Tag as string == AppState.IfppScope)
                {
                    ScopeBox.SelectedIndex = j;
                    break;
                }
            }
            EntityBox.IsEnabled = AppState.IfppScope == "entity";
        }
        finally
        {
            _loadingUi = false;
        }
    }

    private async Task RefreshAsync()
    {
        ErrorBar.IsOpen = false;
        StatusText.Text = "Loading…";
        try
        {
            if (App.Backend is not null)
            {
                var ok = await App.Backend.EnsureRunningAsync();
                if (!ok)
                {
                    StatusText.Text = "Backend offline";
                    ErrorBar.Message = App.Backend.LastError
                        ?? "Cannot reach http://127.0.0.1:7420. Start: python -m financial_os.cli serve";
                    ErrorBar.IsOpen = true;
                    return;
                }
            }

            using var api = new LedgerApiClient();
            await LoadEntitiesAsync(api);
            ApplyScopeFromUi();

            var mode = "conservative";
            if (ModeBox.SelectedItem is ComboBoxItem cbi && cbi.Tag is string tag)
                mode = tag;

            var ifpp = await api.GetIfppAsync(mode);
            CombinedText.Text = Money(ifpp, "combined_purchasing_power");
            CashText.Text = Money(ifpp, "cash_spendable");
            FloatText.Text = Money(ifpp, "card_float_interest_free");
            RedText.Text = ifpp.TryGetProperty("next_red_day", out var rd) && rd.ValueKind != JsonValueKind.Null
                ? rd.GetString() ?? "None"
                : "None";

            var details = ifpp.GetProperty("details");
            var buffer = details.TryGetProperty("safety_buffer", out var b) ? b.GetString() : "?";
            var neverNeg = details.TryGetProperty("never_negative_scope", out var s) ? s.GetString() : "checking";
            var vault = details.TryGetProperty("tax_vault", out var v) ? v.GetString() : "0";
            var ifppScope = JsonUi.Str(ifpp, "ifpp_scope", AppState.IfppScope);
            var scopeNote = details.TryGetProperty("scope_note", out var sn) ? sn.GetString() : "";
            ScopeNote.Text = $"{ifppScope} · {scopeNote}";
            var pendWarn = JsonUi.Str(ifpp, "pending_warning", "");
            if (string.IsNullOrEmpty(pendWarn) || pendWarn == "—")
                pendWarn = details.TryGetProperty("pending_warning", out var pw) && pw.ValueKind == JsonValueKind.String
                    ? pw.GetString() ?? ""
                    : "";
            MetaText.Text =
                $"As of {ifpp.GetProperty("as_of").GetString()} · mode {ifpp.GetProperty("mode").GetString()} · " +
                $"buffer ${buffer} · never-neg {neverNeg} · tax vault ${vault} · IFPP {ifppScope}";
            if (!string.IsNullOrEmpty(pendWarn) && pendWarn != "—")
                MetaText.Text += $" · ⚠ {pendWarn}";
            if (ifpp.TryGetProperty("is_red_now", out var irn) && irn.ValueKind == JsonValueKind.True)
                RedText.Text = "NOW";

            var desk = await api.GetCapitalDeskAsync();
            var head = desk.GetProperty("headline");
            CdAction.Text = (head.GetProperty("action").GetString() ?? "").Replace('_', ' ').ToUpperInvariant();
            CdTitle.Text = head.GetProperty("title").GetString() ?? "—";
            CdAmount.Text = head.GetProperty("amount_hint").GetString() ?? "—";
            CdReason.Text = head.GetProperty("reason").GetString() ?? "";
            DailyHeadline.Text = $"{CdAction.Text}: {CdTitle.Text}";
            DailyReason.Text = CdReason.Text;

            var alts = new List<string>();
            if (head.TryGetProperty("alternatives", out var altArr) && altArr.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in altArr.EnumerateArray())
                    alts.Add("Alt: " + (a.GetString() ?? ""));
            }
            CdAlts.ItemsSource = alts;

            var dig = await api.GetDigestAsync();
            DigestMsg.Text = dig.GetProperty("message").GetString() ?? "";
            var alerts = new List<string>();
            var dailyAlerts = new List<string>();
            if (dig.TryGetProperty("alerts", out var al) && al.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in al.EnumerateArray())
                {
                    var level = a.GetProperty("level").GetString();
                    var msg = a.GetProperty("message").GetString();
                    var line = $"[{level}] {msg}";
                    alerts.Add(line);
                    if (dailyAlerts.Count < 3)
                        dailyAlerts.Add(line);
                }
            }
            if (alerts.Count == 0) alerts.Add("No critical alerts");
            if (dailyAlerts.Count == 0) dailyAlerts.Add("All clear — no action required.");
            DigestAlerts.ItemsSource = alerts;
            DailyAlerts.ItemsSource = dailyAlerts;

            var promo = await api.GetPromoClockAsync();
            var promos = new List<string>();
            if (promo.TryGetProperty("items", out var pi) && pi.ValueKind == JsonValueKind.Array)
            {
                foreach (var p in pi.EnumerateArray())
                {
                    var aid = p.GetProperty("account_id").GetInt32();
                    var name = p.GetProperty("name").GetString();
                    var days = p.GetProperty("days_left").GetInt32();
                    var bal = p.GetProperty("promo_balance").GetString();
                    var sink = p.GetProperty("sinking_fund").GetProperty("monthly").GetString();
                    promos.Add($"#{aid} {name}: {days}d left · balloon ${bal} · sink ${sink}/mo");
                    if (days <= 45 && double.IsNaN(PromoAcctBox.Value))
                        PromoAcctBox.Value = aid;
                }
            }
            if (promos.Count == 0) promos.Add("No active 0% promos tracked.");
            PromoList.ItemsSource = promos;

            if (!_dailyMode)
                await LoadFeesAsync(api);

            try
            {
                var bak = await api.GetBackupStatusAsync();
                StatusText.Text =
                    $"Connected · {(_dailyMode ? "daily" : "full")} · " +
                    $"backups {JsonUi.Str(bak, "backup_count", "0")}";
            }
            catch
            {
                StatusText.Text = $"Connected · {(_dailyMode ? "daily" : "full")}";
            }
        }
        catch (Exception ex)
        {
            StatusText.Text = "Error";
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Fees_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await LoadFeesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task LoadFeesAsync(LedgerApiClient api)
    {
        var fees = await api.GetFeeCandidatesAsync(45, 15);
        FeeSummary.Text =
            $"{JsonUi.Str(fees, "count")} possible fee(s) · ${JsonUi.Str(fees, "total_abs")} · {JsonUi.Str(fees, "principle")}";
        var lines = new List<string>();
        if (fees.TryGetProperty("candidates", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var c in arr.EnumerateArray())
            {
                lines.Add(
                    $"{JsonUi.Str(c, "txn_date")} · {JsonUi.Str(c, "payee")} · " +
                    $"{JsonUi.Money(c, "amount")} · {JsonUi.Str(c, "reason")}");
            }
        }
        if (lines.Count == 0) lines.Add("No fee keywords found in the last 45 days.");
        FeeList.ItemsSource = lines;
    }

    private async void Sink_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (double.IsNaN(PromoAcctBox.Value))
                throw new InvalidOperationException("Enter a promo account id (shown as #id in the list).");
            var id = (int)PromoAcctBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CreatePromoSinkBillAsync(id);
            SinkMsg.Text =
                (res.TryGetProperty("created", out var cr) && cr.GetBoolean() ? "Created" : "Updated") +
                $" · {JsonUi.Str(res, "name")} · {JsonUi.Money(res, "amount")}/mo until {JsonUi.Str(res, "promo_end")}";
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static string Money(JsonElement root, string prop)
    {
        if (!root.TryGetProperty(prop, out var el)) return "—";
        var s = el.ValueKind == JsonValueKind.Number ? el.GetRawText() : el.GetString();
        if (decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return d.ToString("C", CultureInfo.CurrentCulture);
        return s ?? "—";
    }
}
