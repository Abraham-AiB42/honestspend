using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using Windows.UI;

namespace HonestSpend_WinUI.Pages;

public sealed partial class HomePage : Page
{
    private JsonElement _home;
    private string _nextAction = "hold";
    private string _ritualNextAction = "hold";
    private string _booksAction = "hold";
    private string _booksSecondaryAction = "";
    private int? _booksAccountId;
    private int? _promoAccountId;
    private string _monthCloseAction = "hold";
    private int? _monthCloseAccountId;
    private string _taxYearAction = "hold";
    private readonly List<(int TxnId, string Label)> _feeItems = new();
    private int _feeIdx;
    private readonly List<JsonElement> _recurringItems = new();
    private int _recurringIdx;
    /// <summary>Coming up list expanded to full window (via /api/coming-up limit=50).</summary>
    private bool _comingUpExpanded;
    private int _comingUpFullCount;

    public HomePage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        AppState.ScopeChanged += OnAppScopeChanged;
        AppState.ModeChanged += OnAppScopeChanged;
        await RefreshAsync();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        base.OnNavigatedFrom(e);
        AppState.ScopeChanged -= OnAppScopeChanged;
        AppState.ModeChanged -= OnAppScopeChanged;
    }

    private async void OnAppScopeChanged() => await RefreshAsync();

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async Task RefreshAsync()
    {
        ErrorBar.IsOpen = false;
        StatusText.Text = "Loading…";
        try
        {
            EngineBar.IsOpen = false;
            if (App.Backend is not null)
            {
                var ok = await App.Backend.EnsureRunningAsync();
                if (!ok)
                {
                    StatusText.Text = "Engine offline";
                    EngineBar.Message = App.Backend.LastError
                        ?? "Could not start the local engine. If this is a zip install, keep the engine\\ folder next to the app.";
                    EngineBar.IsOpen = true;
                    return;
                }
            }

            using var api = new LedgerApiClient();

            // Store packages: soft license check (does not block reading Home)
            await RefreshLicenseBannerAsync(api);

            _home = await api.GetHomeSimpleAsync();

            SafeText.Text = Money(_home, "safe_to_spend");
            ApplySafeUntilWindow(_home);
            ApplyFloatWhisper(_home);
            await ApplyCardPayWhisperAsync(api);
            await ApplyCardFixHintAsync(api);
            ApplyWhyThisNumber(_home);
            ApplyComingUp(_home);
            ApplyBudgetSummary(_home);
            ApplyBudgetSeedHint(_home);
            var status = JsonUi.Str(_home, "status", "safe");
            StatusLine.Text = JsonUi.Str(_home, "status_label", status);
            StatusLine.Foreground = status switch
            {
                "danger" => new SolidColorBrush(Color.FromArgb(255, 255, 100, 100)),
                "watch" => new SolidColorBrush(Color.FromArgb(255, 255, 180, 60)),
                _ => new SolidColorBrush(Color.FromArgb(255, 80, 200, 120)),
            };

            // One cash spine: risk day from home/IFPP only (same path as Safe to spend).
            // Do not override with a separate cash-runway call (different reserves).
            var risk = JsonUi.Str(_home, "next_risk_day", "");
            RiskLine.Text = string.IsNullOrEmpty(risk) || risk == "—"
                ? "No near-term cash crunch"
                : $"{UiCopy.NextRisk}: {FormatPlainWeekdayDate(risk)} — cash may run short around then";

            var pend = JsonUi.Str(_home, "pending_warning", "");
            PendingLine.Text = (string.IsNullOrEmpty(pend) || pend == "—") ? "" : pend;
            PendingLine.Visibility = string.IsNullOrEmpty(PendingLine.Text)
                ? Visibility.Collapsed
                : Visibility.Visible;

            var who = JsonUi.Str(_home, "who_name");
            if (string.IsNullOrEmpty(who) || who == "—")
                who = "This household";
            var view = JsonUi.Str(_home, "money_view") == "all_money" ? UiCopy.AllMoney : UiCopy.ThisMoney;
            WhoLine.Text = $"{who} · {view}";

            if (_home.TryGetProperty("do_this_next", out var next) && next.ValueKind == JsonValueKind.Object)
            {
                NextTitle.Text = JsonUi.Str(next, "title");
                NextReason.Text = JsonUi.Str(next, "reason");
                NextBtn.Content = JsonUi.Str(next, "button_label", "Continue");
                _nextAction = JsonUi.Str(next, "action", "hold");
                if (next.TryGetProperty("params", out var nparams) && nparams.ValueKind == JsonValueKind.Object)
                {
                    var aid = JsonUi.Int(nparams, "account_id", 0);
                    if (aid > 0)
                    {
                        _booksAccountId = aid;
                        if (_nextAction is "promo_sink" or "promo_balloon")
                            _promoAccountId = aid;
                    }
                }
                var disc = JsonUi.Str(next, "disclaimer", "");
                NextDisclaimer.Text = string.IsNullOrEmpty(disc) || disc == "—" ? "" : disc;
                NextDisclaimer.Visibility = string.IsNullOrEmpty(NextDisclaimer.Text)
                    ? Visibility.Collapsed
                    : Visibility.Visible;

                var alts = new List<string>();
                if (next.TryGetProperty("alternatives", out var aa) && aa.ValueKind == JsonValueKind.Array)
                {
                    foreach (var a in aa.EnumerateArray())
                        alts.Add("· " + (a.GetString() ?? ""));
                }
                AltList.ItemsSource = alts;
            }

            var alerts = new List<string>();
            if (_home.TryGetProperty("alerts", out var al) && al.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in al.EnumerateArray())
                {
                    var level = JsonUi.Str(a, "level");
                    var urgency = level switch
                    {
                        "critical" => "Urgent",
                        "warn" => "Heads-up",
                        "info" => "Note",
                        _ => "",
                    };
                    var title = JsonUi.Str(a, "title");
                    alerts.Add(string.IsNullOrEmpty(urgency) ? title : $"{urgency} · {title}");
                }
            }
            if (alerts.Count == 0) alerts.Add("All clear — no action queue.");
            AlertList.ItemsSource = alerts;

            // Live books / import brief (dream H1-A1) — includes books≠bank honesty
            // Prefer books_brief.account_id; fall back to do_this_next params / ritual.
            _booksAction = "hold";
            _booksSecondaryAction = "";
            var doThisAccountId = _booksAccountId;
            _booksAccountId = null;
            BooksSecondaryBtn.Visibility = Visibility.Collapsed;
            if (_home.TryGetProperty("books_brief", out var books) && books.ValueKind == JsonValueKind.Object)
            {
                var attn = JsonUi.Str(books, "attention", "clear");
                var show = attn is "action" or "watch"
                    || (attn == "optional" && ShouldShowBankTip());
                BooksCard.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
                if (show)
                {
                    BooksTitle.Text = JsonUi.Str(books, "title");
                    BooksReason.Text = JsonUi.Str(books, "reason");
                    _booksAction = JsonUi.Str(books, "primary_action", "review");
                    BooksBtn.Content = JsonUi.Str(books, "button_label", "Continue");
                    var baid = JsonUi.Int(books, "account_id", 0);
                    if (baid > 0)
                        _booksAccountId = baid;
                    var sec = JsonUi.Str(books, "secondary_action");
                    var secLabel = JsonUi.Str(books, "secondary_label");
                    if (!string.IsNullOrEmpty(sec) && sec != "—" && !string.IsNullOrEmpty(secLabel) && secLabel != "—")
                    {
                        _booksSecondaryAction = sec;
                        BooksSecondaryBtn.Content = secLabel;
                        BooksSecondaryBtn.Visibility = Visibility.Visible;
                    }
                    var samples = new List<string>();
                    if (books.TryGetProperty("sample_uncategorized", out var sa) && sa.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var s in sa.EnumerateArray())
                        {
                            var t = s.GetString();
                            if (!string.IsNullOrEmpty(t)) samples.Add("· " + t);
                        }
                    }
                    BooksSamples.ItemsSource = samples;
                }
            }
            else
            {
                BooksCard.Visibility = Visibility.Collapsed;
            }
            if (_booksAccountId is null && doThisAccountId is int dta && dta > 0)
                _booksAccountId = dta;

            // Fee inbox (dream H1-C2) — confirm / dismiss one at a time
            _feeItems.Clear();
            _feeIdx = 0;
            FeeMsg.Text = "";
            if (_home.TryGetProperty("fee_brief", out var fee) && fee.ValueKind == JsonValueKind.Object
                && fee.TryGetProperty("needs_attention", out var fna) && fna.ValueKind == JsonValueKind.True)
            {
                FeeCard.Visibility = Visibility.Visible;
                FeeTitle.Text = JsonUi.Str(fee, "title");
                FeeReason.Text = JsonUi.Str(fee, "reason");
                if (fee.TryGetProperty("items", out var fi) && fi.ValueKind == JsonValueKind.Array)
                {
                    foreach (var it in fi.EnumerateArray())
                    {
                        var tid = JsonUi.Int(it, "transaction_id", 0);
                        if (tid > 0)
                            _feeItems.Add((tid, JsonUi.Str(it, "label", JsonUi.Str(it, "payee"))));
                    }
                }
                ShowFeeItem();
            }
            else
            {
                FeeCard.Visibility = Visibility.Collapsed;
            }

            // Recurring suggestions — one-tap add
            _recurringItems.Clear();
            _recurringIdx = 0;
            RecurringMsg.Text = "";
            if (_home.TryGetProperty("recurring_suggestions", out var rec) && rec.ValueKind == JsonValueKind.Object
                && rec.TryGetProperty("needs_attention", out var rna) && rna.ValueKind == JsonValueKind.True)
            {
                RecurringCard.Visibility = Visibility.Visible;
                RecurringTitle.Text = JsonUi.Str(rec, "title");
                RecurringReason.Text = JsonUi.Str(rec, "reason");
                if (rec.TryGetProperty("suggestions", out var rs) && rs.ValueKind == JsonValueKind.Array)
                {
                    foreach (var s in rs.EnumerateArray())
                        _recurringItems.Add(s.Clone());
                }
                ShowRecurringItem();
            }
            else
            {
                RecurringCard.Visibility = Visibility.Collapsed;
            }

            // Promo set-aside (H1-C3) — do not wipe account id set from do_this_next
            var promoIdFromNext = _promoAccountId;
            if (_home.TryGetProperty("promo_brief", out var promo) && promo.ValueKind == JsonValueKind.Object
                && promo.TryGetProperty("needs_attention", out var pna) && pna.ValueKind == JsonValueKind.True)
            {
                PromoCard.Visibility = Visibility.Visible;
                PromoTitle.Text = JsonUi.Str(promo, "title");
                PromoReason.Text = JsonUi.Str(promo, "reason");
                PromoBtn.Content = JsonUi.Str(promo, "button_label", "Create set-aside");
                PromoMsg.Text = "";
                var paid = JsonUi.Int(promo, "account_id", 0);
                if (paid > 0)
                    _promoAccountId = paid;
                else if (promoIdFromNext is int keep)
                    _promoAccountId = keep;
            }
            else
            {
                PromoCard.Visibility = Visibility.Collapsed;
                // Keep do_this_next / month-close promo account for one-tap CTA
                if (promoIdFromNext is int keep)
                    _promoAccountId = keep;
            }

            // Month-close (H1-B) — hide entirely when period closed (open-rarely)
            _monthCloseAction = "hold";
            _monthCloseAccountId = null;
            if (_home.TryGetProperty("month_close", out var mc) && mc.ValueKind == JsonValueKind.Object)
            {
                var closed = mc.TryGetProperty("closed_this_period", out var cl) && cl.ValueKind == JsonValueKind.True;
                var allDone = mc.TryGetProperty("all_done", out var mad) && mad.ValueKind == JsonValueKind.True;
                var canMark = mc.TryGetProperty("can_mark_closed", out var cm) && cm.ValueKind == JsonValueKind.True;
                if (closed)
                {
                    MonthCloseCard.Visibility = Visibility.Collapsed;
                }
                else
                {
                    MonthCloseCard.Visibility = Visibility.Visible;
                    MonthCloseSubtitle.Text = JsonUi.Str(mc, "subtitle");
                    MonthCloseProgress.Text = JsonUi.Str(mc, "progress_label");
                    var mLines = new List<string>();
                    if (mc.TryGetProperty("steps", out var ms) && ms.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var st in ms.EnumerateArray())
                        {
                            var done = st.TryGetProperty("done", out var d) && d.ValueKind == JsonValueKind.True;
                            var opt = st.TryGetProperty("optional", out var o) && o.ValueKind == JsonValueKind.True;
                            mLines.Add($"{(done ? "✓" : "○")} {JsonUi.Str(st, "title")}" + (opt ? " (optional)" : ""));
                            if (!done && _monthCloseAction == "hold" && !opt)
                            {
                                _monthCloseAction = JsonUi.Str(st, "action", "hold");
                                var maid = JsonUi.Int(st, "account_id", 0);
                                if (maid > 0)
                                    _monthCloseAccountId = maid;
                            }
                        }
                    }
                    MonthCloseList.ItemsSource = mLines;
                    if (canMark || allDone)
                    {
                        MonthCloseList.Visibility = Visibility.Visible;
                        MonthCloseBtn.Visibility = Visibility.Collapsed;
                        MarkMonthClosedBtn.Content = string.IsNullOrEmpty(JsonUi.Str(mc, "button_label"))
                            ? "Mark month closed"
                            : JsonUi.Str(mc, "button_label");
                        MarkMonthClosedBtn.Visibility = Visibility.Visible;
                    }
                    else
                    {
                        MonthCloseList.Visibility = Visibility.Visible;
                        MonthCloseBtn.Content = string.IsNullOrEmpty(JsonUi.Str(mc, "button_label"))
                            ? "Do next close step"
                            : JsonUi.Str(mc, "button_label");
                        MonthCloseBtn.Visibility = Visibility.Visible;
                        MarkMonthClosedBtn.Visibility = Visibility.Collapsed;
                    }
                }
            }
            else
            {
                MonthCloseCard.Visibility = Visibility.Collapsed;
            }

            // Tax year prep (H2-B) — hide when checklist complete
            _taxYearAction = "hold";
            if (_home.TryGetProperty("tax_year", out var ty) && ty.ValueKind == JsonValueKind.Object)
            {
                var tDone = ty.TryGetProperty("all_done", out var tad) && tad.ValueKind == JsonValueKind.True;
                if (tDone)
                {
                    TaxYearCard.Visibility = Visibility.Collapsed;
                }
                else
                {
                    TaxYearCard.Visibility = Visibility.Visible;
                    TaxYearTitle.Text = JsonUi.Str(ty, "title");
                    TaxYearSubtitle.Text = JsonUi.Str(ty, "subtitle");
                    TaxYearProgress.Text = JsonUi.Str(ty, "progress_label");
                    var tLines = new List<string>();
                    if (ty.TryGetProperty("steps", out var ts) && ts.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var st in ts.EnumerateArray())
                        {
                            var done = st.TryGetProperty("done", out var d) && d.ValueKind == JsonValueKind.True;
                            var opt = st.TryGetProperty("optional", out var o) && o.ValueKind == JsonValueKind.True;
                            tLines.Add($"{(done ? "✓" : "○")} {JsonUi.Str(st, "title")}" + (opt ? " (optional)" : ""));
                            if (!done && _taxYearAction == "hold" && !opt)
                                _taxYearAction = JsonUi.Str(st, "action", "hold");
                        }
                    }
                    TaxYearList.ItemsSource = tLines;
                    TaxYearBtn.Visibility = Visibility.Visible;
                }
            }
            else
            {
                TaxYearCard.Visibility = Visibility.Collapsed;
            }

            // 3-minute open-rarely ritual — compact when all clear
            _ritualNextAction = "hold";
            if (_home.TryGetProperty("three_minute_check", out var ritual) && ritual.ValueKind == JsonValueKind.Object)
            {
                var allDone = ritual.TryGetProperty("all_done", out var ad) && ad.ValueKind == JsonValueKind.True;
                RitualSubtitle.Text = JsonUi.Str(ritual, "subtitle", "Open rarely — tick these and close.");
                RitualProgress.Text = JsonUi.Str(ritual, "progress_label", "");
                if (allDone)
                {
                    RitualList.Visibility = Visibility.Collapsed;
                    RitualNextBtn.Visibility = Visibility.Collapsed;
                }
                else
                {
                    RitualList.Visibility = Visibility.Visible;
                    var rLines = new List<string>();
                    if (ritual.TryGetProperty("steps", out var rs) && rs.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var st in rs.EnumerateArray())
                        {
                            var done = st.TryGetProperty("done", out var d) && d.ValueKind == JsonValueKind.True;
                            rLines.Add($"{(done ? "✓" : "○")} {JsonUi.Str(st, "title")} — {JsonUi.Str(st, "detail")}");
                            if (!done && _ritualNextAction == "hold")
                            {
                                _ritualNextAction = JsonUi.Str(st, "action", "hold");
                                // Only fill when books_brief/do_this left account_id empty
                                if (_booksAccountId is null)
                                {
                                    var raid = JsonUi.Int(st, "account_id", 0);
                                    if (raid > 0)
                                        _booksAccountId = raid;
                                }
                            }
                        }
                    }
                    RitualList.ItemsSource = rLines;
                    RitualNextBtn.Visibility = Visibility.Visible;
                    RitualNextBtn.Content = "Do next open item";
                }
            }

            var wealth = new List<string>();
            if (_home.TryGetProperty("wealth_tips", out var wt) && wt.ValueKind == JsonValueKind.Array)
            {
                foreach (var w in wt.EnumerateArray())
                {
                    var title = JsonUi.Str(w, "title");
                    var reason = JsonUi.Str(w, "reason", "");
                    wealth.Add(string.IsNullOrEmpty(reason) || reason == "—"
                        ? $"• {title}"
                        : $"• {title}\n  {reason}");
                }
            }
            WealthList.ItemsSource = wealth;
            WealthCard.Visibility = wealth.Count > 0 ? Visibility.Visible : Visibility.Collapsed;

            if (_home.TryGetProperty("setup", out var su) && su.ValueKind == JsonValueKind.Object)
            {
                var needs = su.TryGetProperty("needs_setup", out var ns) && ns.ValueKind == JsonValueKind.True;
                var hasBill = su.TryGetProperty("has_bill", out var hb) && hb.ValueKind == JsonValueKind.True;
                var hasCash = su.TryGetProperty("has_cash", out var hc) && hc.ValueKind == JsonValueKind.True;
                SetupBar.IsOpen = needs && !AppState.ReadOnlySession;
                EmptyBillBar.IsOpen = !needs && hasCash && !hasBill && !AppState.ReadOnlySession;
                // Keep shell nav in sync (hide Get started after first-run)
                if (AppState.ShowSetupNav != needs)
                {
                    AppState.ShowSetupNav = needs;
                    if (App.MainWindowInstance is MainWindow mw)
                        mw.RefreshSimpleChrome();
                }
                // Soft bank tip once setup is complete (dismissible)
                BankTipBar.IsOpen = !needs && hasCash && !AppState.ReadOnlySession
                    && !EmptyBillBar.IsOpen
                    && ShouldShowBankTip();
            }

            var asOf = JsonUi.Str(_home, "as_of");
            StatusText.Text = string.IsNullOrEmpty(asOf) || asOf == "—"
                ? $"Ready · {view}"
                : $"Ready · {view} · {asOf}";

            ApplySimpleHomeDensity(_home, status);
        }
        catch (Exception ex)
        {
            StatusText.Text = "Error";
            ErrorBar.Message = FriendlyLoadError(ex);
            ErrorBar.IsOpen = true;
        }
    }

    /// <summary>When clear: keep Safe + Do this next + one ritual; collapse the wall of cards.</summary>
    private void ApplySimpleHomeDensity(JsonElement home, string status)
    {
        if (!AppState.SimpleMode)
            return;

        var clear = status is "safe" or "ok" or "clear";
        var nextAct = _nextAction ?? "hold";
        var nextIsQuiet = nextAct is "hold" or "park_yield"
            || nextAct.StartsWith("wealth_", StringComparison.OrdinalIgnoreCase);

        if (status is "danger" or "watch")
        {
            PowerToolsPanel.Visibility = Visibility.Visible;
            return;
        }

        PowerToolsPanel.Visibility = Visibility.Collapsed;

        if (!(clear && nextIsQuiet))
            return;

        // Open-rarely quiet day: hide secondary cards unless they have real work
        try
        {
            // Budgets: hide when empty / no reserve signal
            if (BudgetCard is not null
                && (BudgetSummaryList.ItemsSource is null
                    || (BudgetSummaryList.ItemsSource is System.Collections.ICollection c && c.Count == 0)))
                BudgetCard.Visibility = Visibility.Collapsed;

            if (BooksCard.Visibility == Visibility.Visible
                && string.IsNullOrEmpty(BooksTitle.Text))
                BooksCard.Visibility = Visibility.Collapsed;

            if (FeeCard.Visibility == Visibility.Visible
                && string.IsNullOrEmpty(FeeTitle.Text))
                FeeCard.Visibility = Visibility.Collapsed;

            if (RecurringCard.Visibility == Visibility.Visible
                && (_recurringItems?.Count ?? 0) == 0)
                RecurringCard.Visibility = Visibility.Collapsed;

            if (PromoCard.Visibility == Visibility.Visible
                && string.IsNullOrEmpty(PromoTitle.Text))
                PromoCard.Visibility = Visibility.Collapsed;

            // Month close / tax year: hide when already complete or not needing attention
            if (home.TryGetProperty("month_close", out var mc) && mc.ValueKind == JsonValueKind.Object)
            {
                var closed = mc.TryGetProperty("closed_this_period", out var cl) && cl.ValueKind == JsonValueKind.True;
                var needs = mc.TryGetProperty("needs_attention", out var na) && na.ValueKind == JsonValueKind.True;
                if (closed || !needs)
                    MonthCloseCard.Visibility = Visibility.Collapsed;
            }

            if (home.TryGetProperty("tax_year", out var ty) && ty.ValueKind == JsonValueKind.Object)
            {
                var tNeeds = ty.TryGetProperty("needs_attention", out var tna) && tna.ValueKind == JsonValueKind.True;
                if (!tNeeds)
                    TaxYearCard.Visibility = Visibility.Collapsed;
            }

            // Ritual: collapse when all done
            if (home.TryGetProperty("three_minute_check", out var r) && r.ValueKind == JsonValueKind.Object)
            {
                var allDone = r.TryGetProperty("all_done", out var ad) && ad.ValueKind == JsonValueKind.True;
                if (allDone)
                    RitualCard.Visibility = Visibility.Collapsed;
            }

            // Wealth only when do_this_next is wealth (already primary)
            if (!nextAct.StartsWith("wealth_", StringComparison.OrdinalIgnoreCase)
                && WealthCard is not null)
                WealthCard.Visibility = Visibility.Collapsed;
        }
        catch
        {
            /* controls may vary */
        }
    }

    private void ShowSuccess(string title, string message)
    {
        SuccessBar.Title = title;
        SuccessBar.Message = message;
        SuccessBar.Severity = InfoBarSeverity.Success;
        SuccessBar.IsOpen = true;
        try { SuccessBar.StartBringIntoView(); } catch { /* ignore */ }
    }

    private static string FriendlyLoadError(Exception ex)
    {
        var m = ex.Message ?? "";
        if (m.Contains("refused", StringComparison.OrdinalIgnoreCase)
            || m.Contains("actively refused", StringComparison.OrdinalIgnoreCase)
            || m.Contains("Failed to connect", StringComparison.OrdinalIgnoreCase)
            || m.Contains("No connection", StringComparison.OrdinalIgnoreCase)
            || m.Contains("423", StringComparison.OrdinalIgnoreCase)
            || m.Contains("unavailable", StringComparison.OrdinalIgnoreCase))
            return "Couldn't refresh Safe to spend — open Settings to start the engine, then Refresh.";
        if (m.Length > 160) return m[..157] + "…";
        return m;
    }

    private void NavigateApp(string tag)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic(tag);
        else
            Frame?.Navigate(typeof(HomePage));
    }

    private async Task RefreshLicenseBannerAsync(LedgerApiClient api)
    {
        try
        {
            // Unpackaged / OSS: never nag
            if (!PackageInfo.ShouldEnforceLicense)
            {
                LicenseBar.IsOpen = false;
                return;
            }

            // Best-effort Store sync (silent)
            if (PackageInfo.IsPackaged)
                _ = await StoreLicenseService.SyncToEngineAsync();

            var lic = await api.GetLicenseAsync();
            var licensed = lic.TryGetProperty("licensed", out var l) && l.ValueKind == JsonValueKind.True;
            var enforce = lic.TryGetProperty("enforce", out var e) && e.ValueKind == JsonValueKind.True;
            if (enforce && !licensed)
            {
                LicenseBar.Title = "Purchase required";
                LicenseBar.Message =
                    "Restore your Microsoft Store purchase or activate a license to use the full commercial build.";
                LicenseBar.Severity = InfoBarSeverity.Warning;
                LicenseBar.IsOpen = true;
            }
            else
            {
                LicenseBar.IsOpen = false;
            }
        }
        catch
        {
            LicenseBar.IsOpen = false;
        }
    }

    private void LicenseBar_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("license");
        else
            Frame?.Navigate(typeof(LicensePage));
    }

    private void BudgetsManage_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("budgets");
        else
            Frame?.Navigate(typeof(BudgetsPage));
    }

    private void ApplyBudgetSeedHint(JsonElement home)
    {
        if (home.TryGetProperty("budget_seed_hint", out var h)
            && h.ValueKind == JsonValueKind.Object)
        {
            BudgetSeedBar.Title = JsonUi.Str(h, "title", "Budgets");
            BudgetSeedBar.Message = JsonUi.Str(h, "reason");
            BudgetSeedBar.IsOpen = true;
        }
        else
        {
            BudgetSeedBar.IsOpen = false;
        }
    }

    private async void BudgetSeed_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.SeedBudgetsFromHistoryAsync(AppState.SelectedProfileId, onlyIfEmpty: true);
            BudgetSeedBar.IsOpen = false;
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    /// <summary>
    /// Soft cash after Coming up window outflows (not a replacement for Safe to spend).
    /// e.g. "Until payday: $1,160.00" when the window is payday-driven.
    /// </summary>
    private void ApplySafeUntilWindow(JsonElement home)
    {
        try
        {
            if (!home.TryGetProperty("safe_until_window", out var su) || su.ValueKind != JsonValueKind.Object)
            {
                SafeUntilLine.Text = "";
                SafeUntilLine.Visibility = Visibility.Collapsed;
                return;
            }
            var amt = JsonUi.Money(su, "amount");
            if (string.IsNullOrEmpty(amt) || amt == "—")
            {
                SafeUntilLine.Visibility = Visibility.Collapsed;
                return;
            }
            var label = JsonUi.Str(su, "label", "");
            // Prefer short UI copy: payday vs end of window
            string prefix;
            if (label.Contains("payday", StringComparison.OrdinalIgnoreCase))
                prefix = "Until payday";
            else
                prefix = "Until end of window";
            SafeUntilLine.Text = $"{prefix}: {amt}";
            SafeUntilLine.Visibility = Visibility.Visible;
        }
        catch
        {
            SafeUntilLine.Text = "";
            SafeUntilLine.Visibility = Visibility.Collapsed;
        }
    }

    private void ApplyFloatWhisper(JsonElement home)
    {
        try
        {
            var status = JsonUi.Str(home, "status", "safe");
            var floatAmt = JsonUi.Str(home, "can_charge_no_interest", "0");
            var red = home.TryGetProperty("is_red_now", out var ir) && ir.ValueKind == JsonValueKind.True;
            if (red || status == "danger" || floatAmt is "0" or "0.00" or "—" or "")
            {
                FloatWhisper.Text = "";
                FloatWhisper.Visibility = Visibility.Collapsed;
                return;
            }
            // Optional best card from why lines / ifpp details not always present — keep simple
            FloatWhisper.Text = $"+ {Money(home, "can_charge_no_interest")} interest-free on best card (not added to Safe)";
            FloatWhisper.Visibility = Visibility.Visible;
        }
        catch
        {
            FloatWhisper.Visibility = Visibility.Collapsed;
        }
    }

    /// <summary>
    /// Optional urgency line under float whisper: soonest card payment with amount + due date.
    /// Shows when a payment is due within 14 days and amount &gt; 0.
    /// </summary>
    private async Task ApplyCardPayWhisperAsync(LedgerApiClient api)
    {
        try
        {
            CardPayWhisper.Visibility = Visibility.Collapsed;
            CardPayWhisper.Text = "";
            var cycles = await api.GetAccountCyclesAsync();
            if (!cycles.TryGetProperty("items", out var arr) || arr.ValueKind != JsonValueKind.Array)
                return;

            decimal bestAmt = 0;
            DateTime? bestDue = null;
            var today = DateTime.Today;
            foreach (var it in arr.EnumerateArray())
            {
                var dueStr = JsonUi.Str(it, "next_due", "");
                if (string.IsNullOrEmpty(dueStr) || dueStr == "—") continue;
                if (!DateTime.TryParse(dueStr, CultureInfo.InvariantCulture, DateTimeStyles.None, out var due))
                    continue;
                var payStr = JsonUi.Str(it, "next_payment", "0");
                if (!decimal.TryParse(payStr, NumberStyles.Any, CultureInfo.InvariantCulture, out var pay) || pay <= 0)
                    continue;
                var days = (due.Date - today).TotalDays;
                if (days < 0 || days > 14) continue;
                if (bestDue is null || due.Date < bestDue.Value.Date ||
                    (due.Date == bestDue.Value.Date && pay > bestAmt))
                {
                    bestDue = due.Date;
                    bestAmt = pay;
                }
            }

            // Sum all payments on that soonest due date for a single plain-language line
            if (bestDue is null) return;
            decimal sum = 0;
            foreach (var it in arr.EnumerateArray())
            {
                var dueStr = JsonUi.Str(it, "next_due", "");
                if (!DateTime.TryParse(dueStr, CultureInfo.InvariantCulture, DateTimeStyles.None, out var due))
                    continue;
                if (due.Date != bestDue.Value.Date) continue;
                var payStr = JsonUi.Str(it, "next_payment", "0");
                if (decimal.TryParse(payStr, NumberStyles.Any, CultureInfo.InvariantCulture, out var pay) && pay > 0)
                    sum += pay;
            }
            if (sum <= 0) return;

            CardPayWhisper.Text =
                $"Next card payments: {sum.ToString("C", CultureInfo.CurrentCulture)} on {bestDue.Value:MMM d}";
            CardPayWhisper.Visibility = Visibility.Visible;
        }
        catch
        {
            CardPayWhisper.Visibility = Visibility.Collapsed;
        }
    }

    /// <summary>
    /// Simple-mode banner when credit cards exist but payment setup is incomplete
    /// (missing due day, pay-from cash, or what-to-pay is none/empty).
    /// </summary>
    private async Task ApplyCardFixHintAsync(LedgerApiClient api)
    {
        try
        {
            CardFixBar.IsOpen = false;
            if (!AppState.SimpleMode || AppState.ReadOnlySession)
                return;

            var cycles = await api.GetAccountCyclesAsync();
            if (!cycles.TryGetProperty("items", out var arr) || arr.ValueKind != JsonValueKind.Array)
                return;

            var hasCards = false;
            var needsSetup = false;
            foreach (var it in arr.EnumerateArray())
            {
                hasCards = true;
                if (CardNeedsPaymentSetup(it))
                {
                    needsSetup = true;
                    break;
                }
            }

            CardFixBar.IsOpen = hasCards && needsSetup;
            if (!needsSetup && CardFixPanel.Visibility == Visibility.Visible)
            {
                // Keep panel open if user is mid-edit after partial save of one of several cards
            }
        }
        catch
        {
            CardFixBar.IsOpen = false;
        }
    }

    private static bool CardNeedsPaymentSetup(JsonElement it)
    {
        // Missing due day (null / 0 / absent)
        var dueMissing = !it.TryGetProperty("payment_due_day", out var dueEl)
            || dueEl.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined
            || (dueEl.ValueKind == JsonValueKind.Number && dueEl.TryGetInt32(out var d) && d < 1)
            || (dueEl.ValueKind == JsonValueKind.String
                && (!int.TryParse(dueEl.GetString(), out var ds) || ds < 1));

        // Missing pay-from cash
        var fundId = JsonUi.Int(it, "payment_funding_account_id",
            JsonUi.Int(it, "funding_account_id"));
        var fundMissing = fundId <= 0;

        // Nothing configured for what to pay
        var policy = JsonUi.Str(it, "autopay_policy", JsonUi.Str(it, "policy", "")).Trim();
        if (policy == "—") policy = "";
        var policyNone = string.IsNullOrEmpty(policy)
            || policy.Equals("none", StringComparison.OrdinalIgnoreCase);

        return dueMissing || fundMissing || policyNone;
    }

    private async void CardFixOpen_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            CardFixMsg.Text = "";
            CardFixPanel.Visibility = Visibility.Visible;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await LoadCardFixPanelAsync(api);
            try { CardFixPanel.StartBringIntoView(); } catch { /* ignore */ }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void CardFixClose_Click(object sender, RoutedEventArgs e)
    {
        CardFixPanel.Visibility = Visibility.Collapsed;
        CardFixMsg.Text = "";
    }

    private bool _suppressCardFixCardChange;

    private async Task LoadCardFixPanelAsync(LedgerApiClient api)
    {
        CardFixFundingBox.Items.Clear();
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
                    CardFixFundingBox.Items.Add(new ComboBoxItem
                    {
                        Content = $"{name} · {UiCopy.AccountKind(kind)} · {JsonUi.Money(a, "current_balance")}",
                        Tag = id,
                    });
                }
            }
        }
        if (CardFixFundingBox.Items.Count > 0)
            CardFixFundingBox.SelectedIndex = 0;

        var cycles = await api.GetAccountCyclesAsync();
        var prevId = SelectedCardFixCardId();
        _suppressCardFixCardChange = true;
        CardFixCardBox.Items.Clear();
        var firstNeedsIdx = -1;
        if (cycles.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            var idx = 0;
            foreach (var it in arr.EnumerateArray())
            {
                var id = JsonUi.Int(it, "account_id");
                var name = JsonUi.Str(it, "name");
                var needs = CardNeedsPaymentSetup(it);
                CardFixCardBox.Items.Add(new ComboBoxItem
                {
                    Content = needs ? $"{name} · needs setup" : name,
                    Tag = id,
                });
                if (needs && firstNeedsIdx < 0)
                    firstNeedsIdx = idx;
                idx++;
            }
        }

        if (CardFixCardBox.Items.Count > 0)
        {
            var sel = firstNeedsIdx >= 0 ? firstNeedsIdx : 0;
            if (prevId is int keep)
            {
                for (var i = 0; i < CardFixCardBox.Items.Count; i++)
                {
                    if (CardFixCardBox.Items[i] is ComboBoxItem { Tag: int tid } && tid == keep)
                    {
                        sel = i;
                        break;
                    }
                }
            }
            CardFixCardBox.SelectedIndex = sel;
        }
        _suppressCardFixCardChange = false;

        await ApplySelectedCardFixAsync(api);
        CardFixPolicy_Changed(CardFixPolicyBox, null!);
    }

    private int? SelectedCardFixCardId()
    {
        if (CardFixCardBox.SelectedItem is ComboBoxItem { Tag: int id } && id > 0)
            return id;
        return null;
    }

    private async void CardFixCard_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressCardFixCardChange) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await ApplySelectedCardFixAsync(api);
        }
        catch (Exception ex)
        {
            CardFixMsg.Text = ex.Message;
        }
    }

    private async Task ApplySelectedCardFixAsync(LedgerApiClient api)
    {
        if (SelectedCardFixCardId() is not int id)
            return;

        try
        {
            var c = await api.GetAccountCycleAsync(id);
            CardFixCloseDayBox.Value = ParseCardFixDay(c, "statement_close_day", 1);
            CardFixDueDayBox.Value = ParseCardFixDay(c, "payment_due_day", 15);
            SelectCardFixTag(CardFixPolicyBox,
                JsonUi.Str(c, "policy", JsonUi.Str(c, "autopay_policy", "statement")),
                fallback: "statement");
            SelectCardFixTag(CardFixTimingBox,
                JsonUi.Str(c, "payment_timing", "on_due"),
                fallback: "on_due");
            var fixedAmt = ParseCardFixDouble(c, "payment_fixed_amount", 0);
            CardFixFixedBox.Value = fixedAmt;

            var fundId = JsonUi.Int(c, "funding_account_id", JsonUi.Int(c, "payment_funding_account_id"));
            SelectCardFixIntTag(CardFixFundingBox, fundId);
            CardFixPolicy_Changed(CardFixPolicyBox, null!);
        }
        catch (Exception ex)
        {
            CardFixMsg.Text = "Could not load card: " + ex.Message;
        }
    }

    private void CardFixPolicy_Changed(object sender, SelectionChangedEventArgs e)
    {
        var policy = "statement";
        if (CardFixPolicyBox.SelectedItem is ComboBoxItem { Tag: string p })
            policy = p;
        CardFixFixedBox.Visibility = policy == "fixed" ? Visibility.Visible : Visibility.Collapsed;
    }

    private async void CardFixSave_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        CardFixMsg.Text = "";
        try
        {
            if (SelectedCardFixCardId() is not int id)
                throw new InvalidOperationException("Pick a credit card.");

            if (CardFixFundingBox.SelectedItem is not ComboBoxItem { Tag: int fundId } || fundId <= 0)
                throw new InvalidOperationException("Pick which cash account pays this card.");

            var policy = "statement";
            if (CardFixPolicyBox.SelectedItem is ComboBoxItem { Tag: string p })
                policy = p;
            var timing = "on_due";
            if (CardFixTimingBox.SelectedItem is ComboBoxItem { Tag: string t })
                timing = t;

            var due = double.IsNaN(CardFixDueDayBox.Value) ? 15 : (int)CardFixDueDayBox.Value;
            var close = double.IsNaN(CardFixCloseDayBox.Value) ? 1 : (int)CardFixCloseDayBox.Value;
            if (due is < 1 or > 31)
                throw new InvalidOperationException("Due day must be 1–31.");
            if (close is < 1 or > 31)
                throw new InvalidOperationException("Close day must be 1–31.");

            var body = new Dictionary<string, object?>
            {
                ["statement_close_day"] = close,
                ["payment_due_day"] = due,
                ["autopay_policy"] = policy,
                ["payment_timing"] = timing,
                ["payment_funding_account_id"] = fundId,
            };

            if (policy == "fixed")
            {
                var amt = double.IsNaN(CardFixFixedBox.Value) ? 0m : (decimal)CardFixFixedBox.Value;
                if (amt <= 0)
                    throw new InvalidOperationException("Enter a fixed amount greater than zero.");
                body["payment_fixed_amount"] = amt;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.PutAccountCycleConfigAsync(id, body);
            CardFixMsg.Text = "Saved · next payment will update Safe to spend";
            ShowSuccess("Card payment", "Saved · next payment will update Safe to spend");
            await LoadCardFixPanelAsync(api);
            await ApplyCardFixHintAsync(api);
            await ApplyCardPayWhisperAsync(api);
            // Soft refresh Safe / coming up so schedule changes show up
            try
            {
                _home = await api.GetHomeSimpleAsync();
                SafeText.Text = Money(_home, "safe_to_spend");
                ApplySafeUntilWindow(_home);
                ApplyComingUp(_home);
            }
            catch { /* optional */ }
        }
        catch (Exception ex)
        {
            CardFixMsg.Text = ex.Message;
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static double ParseCardFixDay(JsonElement el, string prop, double fallback)
    {
        if (!el.TryGetProperty(prop, out var p) || p.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            return fallback;
        if (p.ValueKind == JsonValueKind.Number && p.TryGetDouble(out var d) && d >= 1 && d <= 31)
            return d;
        if (p.ValueKind == JsonValueKind.String
            && double.TryParse(p.GetString(), NumberStyles.Any, CultureInfo.InvariantCulture, out var s)
            && s >= 1 && s <= 31)
            return s;
        return fallback;
    }

    private static double ParseCardFixDouble(JsonElement el, string prop, double fallback)
    {
        if (!el.TryGetProperty(prop, out var p) || p.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            return fallback;
        if (p.ValueKind == JsonValueKind.Number && p.TryGetDouble(out var d))
            return d;
        if (p.ValueKind == JsonValueKind.String
            && double.TryParse(p.GetString(), NumberStyles.Any, CultureInfo.InvariantCulture, out var s))
            return s;
        return fallback;
    }

    private static void SelectCardFixTag(ComboBox box, string value, string fallback)
    {
        var want = (value ?? fallback).ToLowerInvariant();
        if (want is "none" or "" or "—")
            want = fallback;
        // map alternate aliases
        if (want is "pay_current") want = "books";
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem { Tag: string t }
                && string.Equals(t, want, StringComparison.OrdinalIgnoreCase))
            {
                box.SelectedIndex = i;
                return;
            }
        }
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem { Tag: string t }
                && string.Equals(t, fallback, StringComparison.OrdinalIgnoreCase))
            {
                box.SelectedIndex = i;
                return;
            }
        }
        if (box.Items.Count > 0)
            box.SelectedIndex = 0;
    }

    private static void SelectCardFixIntTag(ComboBox box, int id)
    {
        if (id > 0)
        {
            for (var i = 0; i < box.Items.Count; i++)
            {
                if (box.Items[i] is ComboBoxItem { Tag: int t } && t == id)
                {
                    box.SelectedIndex = i;
                    return;
                }
            }
        }
        if (box.Items.Count > 0)
            box.SelectedIndex = 0;
    }

    private void ApplyWhyThisNumber(JsonElement home)
    {
        try
        {
            if (!home.TryGetProperty("why_this_number", out var why) || why.ValueKind != JsonValueKind.Object)
            {
                WhyOneLiner.Text = "";
                WhyList.ItemsSource = null;
                return;
            }
            var one = JsonUi.Str(why, "one_liner");
            WhyOneLiner.Text = (string.IsNullOrEmpty(one) || one == "—") ? "" : one;
            var lines = new List<string>();
            if (why.TryGetProperty("lines", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var line in arr.EnumerateArray())
                {
                    var s = line.GetString();
                    if (!string.IsNullOrWhiteSpace(s))
                        lines.Add("· " + s);
                }
            }
            WhyList.ItemsSource = lines.Count > 0 ? lines : null;
        }
        catch
        {
            WhyOneLiner.Text = "";
            WhyList.ItemsSource = null;
        }
    }

    /// <summary>
    /// Coming up strip from home/simple <c>coming_up</c> (no extra round-trip).
    /// Rows: weekday · name · money; inflows prefixed with +.
    /// Outflows fill ComingUpPayPick for mark-paid.
    /// Resets expand state (refresh path).
    /// </summary>
    private void ApplyComingUp(JsonElement home)
    {
        _comingUpExpanded = false;
        _comingUpFullCount = 0;
        try
        {
            if (!home.TryGetProperty("coming_up", out var cu) || cu.ValueKind != JsonValueKind.Object)
            {
                ComingUpCard.Visibility = Visibility.Collapsed;
                ComingUpPayPanel.Visibility = Visibility.Collapsed;
                ComingUpShowAllBtn.Visibility = Visibility.Collapsed;
                return;
            }

            BindComingUp(cu, expanded: false);
        }
        catch
        {
            ComingUpCard.Visibility = Visibility.Collapsed;
            ComingUpPayPanel.Visibility = Visibility.Collapsed;
            ComingUpShowAllBtn.Visibility = Visibility.Collapsed;
        }
    }

    /// <summary>
    /// Bind Coming up list + mark-paid picker from a <c>coming_up</c> JSON object.
    /// When truncated and not expanded, shows <b>Show all (N)</b>; when expanded, <b>Show less</b>.
    /// </summary>
    private void BindComingUp(JsonElement cu, bool expanded)
    {
        ComingUpCard.Visibility = Visibility.Visible;

        var subtitle = "";
        if (cu.TryGetProperty("window", out var win) && win.ValueKind == JsonValueKind.Object)
            subtitle = JsonUi.Str(win, "label", "");
        if (string.IsNullOrEmpty(subtitle) || subtitle == "—")
            subtitle = "";
        ComingUpSubtitle.Text = subtitle;
        ComingUpSubtitle.Visibility = string.IsNullOrEmpty(subtitle)
            ? Visibility.Collapsed
            : Visibility.Visible;

        var lines = new List<string>();
        ComingUpPayPick.Items.Clear();
        if (cu.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
        {
            foreach (var it in items.EnumerateArray())
            {
                var weekday = JsonUi.Str(it, "weekday", "");
                var name = JsonUi.Str(it, "name", "");
                var direction = JsonUi.Str(it, "direction", "out");
                var money = FormatComingUpMoney(it, direction);
                if (string.IsNullOrEmpty(weekday) || weekday == "—")
                    weekday = "?";
                if (string.IsNullOrEmpty(name) || name == "—")
                    name = "Item";
                lines.Add($"{weekday} · {name} · {money}");

                // Mark paid: outflows only, need scheduled_id (includes expanded rows)
                if (direction != "out")
                    continue;
                var sid = JsonUi.Int(it, "scheduled_id", 0);
                if (sid <= 0)
                    continue;
                ComingUpPayPick.Items.Add(new ComboBoxItem
                {
                    Content = $"{weekday} · {name} · {money}",
                    Tag = sid,
                });
            }
        }

        if (ComingUpPayPick.Items.Count > 0)
        {
            ComingUpPayPick.SelectedIndex = 0;
            ComingUpPayPanel.Visibility = Visibility.Visible;
        }
        else
        {
            ComingUpPayPanel.Visibility = Visibility.Collapsed;
        }

        var truncated = cu.TryGetProperty("truncated", out var tr) && tr.ValueKind == JsonValueKind.True;
        var fullCount = JsonUi.Int(cu, "count", lines.Count);
        var shown = JsonUi.Int(cu, "shown_count", lines.Count);
        if (fullCount > 0)
            _comingUpFullCount = fullCount;

        if (lines.Count == 0)
        {
            ComingUpList.ItemsSource = null;
            ComingUpList.Visibility = Visibility.Collapsed;
            var hint = JsonUi.Str(cu, "empty_hint", "");
            if (string.IsNullOrEmpty(hint) || hint == "—")
                hint = "Nothing scheduled in this window — add a bill or paycheck in Add";
            ComingUpEmpty.Text = hint;
            ComingUpEmpty.Visibility = Visibility.Visible;
            ComingUpTotals.Text = "";
            ComingUpTotals.Visibility = Visibility.Collapsed;
            ComingUpShowAllBtn.Visibility = Visibility.Collapsed;
        }
        else
        {
            ComingUpList.ItemsSource = lines;
            ComingUpList.Visibility = Visibility.Visible;
            ComingUpEmpty.Text = "";
            ComingUpEmpty.Visibility = Visibility.Collapsed;

            var outAbs = ParseMoneyAbs(cu, "outflow_total");
            var inAbs = ParseMoneyAbs(cu, "inflow_total");
            var parts = new List<string>();
            if (outAbs > 0)
                parts.Add($"Out {outAbs.ToString("C", CultureInfo.CurrentCulture)}");
            if (inAbs > 0)
                parts.Add($"In +{inAbs.ToString("C", CultureInfo.CurrentCulture)}");
            // Totals are for the full window; hide "showing" when expanded
            if (!expanded && truncated && fullCount > shown)
                parts.Add($"showing {shown} of {fullCount}");
            ComingUpTotals.Text = parts.Count > 0 ? string.Join(" · ", parts) : "";
            ComingUpTotals.Visibility = string.IsNullOrEmpty(ComingUpTotals.Text)
                ? Visibility.Collapsed
                : Visibility.Visible;

            // Show all (N) when embedded list is truncated; Show less when expanded
            if (expanded)
            {
                ComingUpShowAllBtn.Content = "Show less";
                ComingUpShowAllBtn.Visibility = Visibility.Visible;
            }
            else if (truncated && fullCount > shown)
            {
                ComingUpShowAllBtn.Content = $"Show all ({fullCount})";
                ComingUpShowAllBtn.Visibility = Visibility.Visible;
            }
            else
            {
                ComingUpShowAllBtn.Visibility = Visibility.Collapsed;
            }
        }
    }

    /// <summary>
    /// Expand Coming up to full window items (API limit=50) or collapse back to home strip.
    /// </summary>
    private async void ComingUpShowAll_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (_comingUpExpanded)
            {
                // Collapse: re-bind embedded home/simple list
                if (_home.ValueKind == JsonValueKind.Object
                    && _home.TryGetProperty("coming_up", out var cu)
                    && cu.ValueKind == JsonValueKind.Object)
                {
                    _comingUpExpanded = false;
                    BindComingUp(cu, expanded: false);
                }
                else
                {
                    ApplyComingUp(_home);
                }
                return;
            }

            ComingUpShowAllBtn.IsEnabled = false;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            // API caps at 50; request full window so mark-paid covers every cash outflow
            var limit = _comingUpFullCount > 0 ? Math.Min(Math.Max(_comingUpFullCount, 8), 50) : 50;
            var full = await api.GetComingUpAsync(limit: limit);
            _comingUpExpanded = true;
            BindComingUp(full, expanded: true);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = "Could not load full Coming up list: " + ex.Message;
            ErrorBar.IsOpen = true;
        }
        finally
        {
            ComingUpShowAllBtn.IsEnabled = true;
        }
    }

    private async void ComingUpMarkPaid_Click(object sender, RoutedEventArgs e)
    {
        if (ComingUpPayPick.SelectedItem is not ComboBoxItem pick || pick.Tag is not int scheduledId || scheduledId <= 0)
        {
            ErrorBar.Message = "Pick an expense to mark paid.";
            ErrorBar.IsOpen = true;
            return;
        }

        var label = pick.Content?.ToString() ?? "expense";
        try
        {
            ComingUpMarkPaidBtn.IsEnabled = false;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            JsonElement res;
            try
            {
                res = await api.MarkSchedulePaidAsync(scheduledId, createTransaction: true);
            }
            catch (Exception ex) when (NeverNegUi.TryParseWouldGoNegative(ex, out var confirmRequired, out var engMsg))
            {
                var friendly = NeverNegUi.FriendlyMessage(engMsg);
                if (!confirmRequired)
                {
                    ErrorBar.Message = friendly;
                    ErrorBar.IsOpen = true;
                    return;
                }

                var dlg = new ContentDialog
                {
                    Title = "Checking would go negative",
                    Content = "This would make checking negative. Mark paid anyway?",
                    PrimaryButtonText = "Yes",
                    CloseButtonText = "No",
                    DefaultButton = ContentDialogButton.Close,
                    XamlRoot = XamlRoot,
                };
                if (await dlg.ShowAsync() != ContentDialogResult.Primary)
                {
                    ErrorBar.Message = friendly;
                    ErrorBar.IsOpen = true;
                    return;
                }

                res = await api.MarkSchedulePaidAsync(scheduledId, createTransaction: true, confirmUnsafe: true);
            }

            var name = JsonUi.Str(res, "name", label);
            var next = JsonUi.Str(res, "next_date", "");
            var ended = res.TryGetProperty("ended", out var en) && en.ValueKind == JsonValueKind.True;
            var msg = ended
                ? $"{name} paid through end"
                : string.IsNullOrEmpty(next) || next == "—"
                    ? $"{name} marked paid"
                    : $"{name} marked paid · next {next}";
            await RefreshAsync();
            ShowSuccess("Marked paid", msg);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = FriendlyLoadError(ex);
            ErrorBar.IsOpen = true;
        }
        finally
        {
            ComingUpMarkPaidBtn.IsEnabled = true;
        }
    }

    /// <summary>
    /// ISO date (yyyy-MM-dd) → plain English short weekday like Coming up, e.g. "Fri Mar 6".
    /// Unparseable values pass through unchanged.
    /// </summary>
    private static string FormatPlainWeekdayDate(string isoOrDate)
    {
        if (string.IsNullOrWhiteSpace(isoOrDate))
            return isoOrDate;
        if (DateOnly.TryParse(isoOrDate, CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
            return d.ToString("ddd MMM d", CultureInfo.InvariantCulture);
        if (DateTime.TryParse(isoOrDate, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var dt))
            return dt.ToString("ddd MMM d", CultureInfo.InvariantCulture);
        return isoOrDate;
    }

    private static string FormatComingUpMoney(JsonElement it, string direction)
    {
        var s = JsonUi.Str(it, "amount", "");
        if (!decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return JsonUi.Money(it, "amount");
        var abs = Math.Abs(d);
        var formatted = abs.ToString("C", CultureInfo.CurrentCulture);
        // Inflows: +$; outflows: absolute currency (product copy, not signed)
        if (direction == "in" || d > 0)
            return "+" + formatted;
        return formatted;
    }

    private static decimal ParseMoneyAbs(JsonElement el, string prop)
    {
        var s = JsonUi.Str(el, prop, "0");
        if (decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return Math.Abs(d);
        return 0;
    }

    private void ApplyBudgetSummary(JsonElement home)
    {
        var reserve = JsonUi.Str(home, "budget_reserve", "0");
        var before = JsonUi.Str(home, "safe_to_spend_before_budgets", "");
        var view = JsonUi.Str(home, "money_view");
        if (view == "all_money")
        {
            BudgetScopeNote.Text =
                $"All money: ${reserve} reserved across every money pile. Budget cards below are for the selected Who only.";
            BudgetScopeNote.Visibility = Visibility.Visible;
        }
        else
        {
            BudgetScopeNote.Text = "";
            BudgetScopeNote.Visibility = Visibility.Collapsed;
        }
        if (!string.IsNullOrEmpty(reserve) && reserve != "0" && reserve != "0.00" && reserve != "—")
        {
            BudgetReserveLine.Text = string.IsNullOrEmpty(before) || before == "—"
                ? $"${reserve} held in category budgets"
                : $"${reserve} in budgets (was ${before} before reserve)";
            BudgetReserveLine.Visibility = Visibility.Visible;
        }
        else
        {
            BudgetReserveLine.Text = "";
            BudgetReserveLine.Visibility = Visibility.Collapsed;
        }

        var lines = new List<string>();
        BudgetCutPanel.Children.Clear();
        var cutCount = 0;
        if (home.TryGetProperty("budgets", out var b) && b.ValueKind == JsonValueKind.Object)
        {
            if (b.TryGetProperty("summary", out var sum) && sum.ValueKind == JsonValueKind.Array)
            {
                foreach (var it in sum.EnumerateArray())
                {
                    lines.Add(
                        $"{JsonUi.Str(it, "name")} ({JsonUi.Str(it, "period")}): " +
                        $"${JsonUi.Str(it, "remaining")} left of ${JsonUi.Str(it, "plan")}");
                }
            }
            if (b.TryGetProperty("cut_offers", out var co) && co.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in co.EnumerateArray())
                {
                    var ruleId = JsonUi.Int(o, "budget_rule_id", 0);
                    var kind = JsonUi.Str(o, "kind");
                    var label = JsonUi.Str(o, "label");
                    var free = JsonUi.Str(o, "free_amount");
                    if (ruleId <= 0 || string.IsNullOrEmpty(kind))
                        continue;
                    var dict = new Dictionary<string, object?>();
                    if (o.TryGetProperty("params", out var pr) && pr.ValueKind == JsonValueKind.Object)
                    {
                        foreach (var prop in pr.EnumerateObject())
                        {
                            dict[prop.Name] = prop.Value.ValueKind switch
                            {
                                JsonValueKind.Number when prop.Value.TryGetInt32(out var i) => i,
                                JsonValueKind.Number => prop.Value.GetDouble(),
                                JsonValueKind.String => prop.Value.GetString(),
                                JsonValueKind.True => true,
                                JsonValueKind.False => false,
                                _ => prop.Value.GetRawText(),
                            };
                        }
                    }
                    var btn = new Button
                    {
                        Content = $"{label} · free ${free}",
                        HorizontalAlignment = HorizontalAlignment.Left,
                        Tag = (ruleId, kind, dict),
                    };
                    btn.Click += BudgetCut_Click;
                    BudgetCutPanel.Children.Add(btn);
                    cutCount++;
                    if (cutCount >= 4)
                        break;
                }
            }
        }
        BudgetSummaryList.ItemsSource = lines;
        BudgetCard.Visibility = lines.Count > 0 || cutCount > 0
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private async void BudgetCut_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ValueTuple<int, string, Dictionary<string, object?>> tag)
            return;
        var (ruleId, kind, paramsObj) = tag;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.ApplyBudgetCutAsync(ruleId, kind, paramsObj, "Applied from Home");
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static bool ShouldShowBankTip()
    {
        try
        {
            var ls = Windows.Storage.ApplicationData.Current.LocalSettings.Values;
            return ls["BankTipDismissed"] is not true;
        }
        catch
        {
            return true;
        }
    }

    private void RitualNext_Click(object sender, RoutedEventArgs e)
    {
        _nextAction = _ritualNextAction;
        DoNext_Click(sender, e);
    }

    private async Task TrustBooksFromBankAsync()
    {
        try
        {
            if (_booksAccountId is not int aid)
            {
                NavigateApp("import");
                return;
            }
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ReconcileTrustAsync(aid, "institution");
            var msg =
                $"Safe to spend now matches bank · books ${JsonUi.Str(res, "books_balance")}.";
            await RefreshAsync();
            ShowSuccess("Books trusted", msg);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = FriendlyLoadError(ex);
            ErrorBar.IsOpen = true;
        }
    }

    private async void Books_Click(object sender, RoutedEventArgs e)
    {
        if (_booksAction is "set_books_from_bank")
        {
            await TrustBooksFromBankAsync();
            return;
        }
        _nextAction = _booksAction switch
        {
            "review" => "review",
            "plaid" => "plaid",
            "ledger" => "ledger",
            "import" => "import",
            _ => "review",
        };
        // Navigate directly for surfaces not in DoNext switch
        if (_booksAction is "plaid")
        {
            Frame?.Navigate(typeof(PlaidPage));
            return;
        }
        if (_booksAction is "import")
        {
            Frame?.Navigate(typeof(ImportPage));
            return;
        }
        DoNext_Click(sender, e);
    }

    private void ShowFeeItem()
    {
        if (_feeIdx >= _feeItems.Count)
        {
            FeeItemLabel.Text = "Queue clear.";
            return;
        }
        var (id, label) = _feeItems[_feeIdx];
        FeeItemLabel.Text = $"{_feeIdx + 1}/{_feeItems.Count}: {label}";
    }

    private async void FeeConfirm_Click(object sender, RoutedEventArgs e)
        => await FeeActAsync("mark_fee");

    private async void FeeDismiss_Click(object sender, RoutedEventArgs e)
        => await FeeActAsync("dismiss");

    private void FeeSkip_Click(object sender, RoutedEventArgs e)
    {
        _feeIdx++;
        if (_feeIdx >= _feeItems.Count)
            FeeCard.Visibility = Visibility.Collapsed;
        else
            ShowFeeItem();
    }

    private async Task FeeActAsync(string action)
    {
        ErrorBar.IsOpen = false;
        if (_feeIdx >= _feeItems.Count) return;
        try
        {
            var id = _feeItems[_feeIdx].TxnId;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.ConfirmFeeAsync(id, action);
            FeeMsg.Text = action == "mark_fee" ? "Marked as fee." : "Dismissed.";
            _feeIdx++;
            if (_feeIdx >= _feeItems.Count)
                await RefreshAsync();
            else
                ShowFeeItem();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void ShowRecurringItem()
    {
        if (_recurringIdx >= _recurringItems.Count)
        {
            RecurringItemLabel.Text = "Queue clear.";
            return;
        }
        var s = _recurringItems[_recurringIdx];
        var pays = JsonUi.Str(s, "pays_from", "cash");
        var acct = JsonUi.Str(s, "suggested_account_name", "");
        var acctBit = string.IsNullOrEmpty(acct) || acct == "—"
            ? ""
            : pays == "card" ? $" · card {acct}" : $" · {acct}";
        RecurringItemLabel.Text =
            $"{_recurringIdx + 1}/{_recurringItems.Count}: {JsonUi.Str(s, "name")} · " +
            $"${JsonUi.Str(s, "amount_abs")}/{JsonUi.Str(s, "cadence")}{acctBit}";
    }

    private async void RecurringAccept_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        if (_recurringIdx >= _recurringItems.Count) return;
        try
        {
            var s = _recurringItems[_recurringIdx];
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var nextRaw = JsonUi.Str(s, "suggested_next_date", "");
            var aid = JsonUi.Int(s, "suggested_account_id", 0);
            if (aid <= 0)
                aid = JsonUi.Int(s, "account_id", 0);
            var body = new Dictionary<string, object?>
            {
                ["name"] = JsonUi.Str(s, "name"),
                ["amount"] = decimal.TryParse(JsonUi.Str(s, "amount_abs"), System.Globalization.NumberStyles.Any, System.Globalization.CultureInfo.InvariantCulture, out var a) ? a : 0m,
                ["cadence"] = JsonUi.Str(s, "cadence", "monthly"),
                ["next_date"] = string.IsNullOrEmpty(nextRaw) || nextRaw == "—" ? null : nextRaw,
                ["profile_id"] = AppState.SelectedProfileId,
                ["account_id"] = aid > 0 ? aid : null,
            };
            var res = await api.AcceptRecurringAsync(body);
            var where = aid > 0 && JsonUi.Str(s, "pays_from") == "card" ? " on card" : "";
            RecurringMsg.Text = $"Added{where} · {JsonUi.Str(res, "name")} · {JsonUi.Str(res, "cadence")}";
            _recurringIdx++;
            if (_recurringIdx >= _recurringItems.Count)
                await RefreshAsync();
            else
                ShowRecurringItem();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void RecurringSkip_Click(object sender, RoutedEventArgs e)
    {
        _recurringIdx++;
        if (_recurringIdx >= _recurringItems.Count)
            RecurringCard.Visibility = Visibility.Collapsed;
        else
            ShowRecurringItem();
    }

    private void Recurring_Click(object sender, RoutedEventArgs e)
        => Frame?.Navigate(typeof(MoneyWizardPage), "bill");

    private async void Promo_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (_promoAccountId is not int id)
            {
                Frame?.Navigate(typeof(CreditPage));
                return;
            }
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CreatePromoSinkBillAsync(id);
            var msg =
                $"Set-aside ready · {JsonUi.Str(res, "name")} · ${JsonUi.Str(res, "monthly_sink")}/mo";
            await RefreshAsync();
            ShowSuccess("0% set-aside", msg);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = FriendlyLoadError(ex);
            ErrorBar.IsOpen = true;
        }
    }

    private async void MonthClose_Click(object sender, RoutedEventArgs e)
    {
        switch (_monthCloseAction)
        {
            case "review":
                NavigateApp("review");
                break;
            case "fees":
                _nextAction = "fees";
                DoNext_Click(sender, e);
                break;
            case "promo_sink":
                if (_monthCloseAccountId is int pid)
                {
                    _promoAccountId = pid;
                    await Promo_Click_Core(pid);
                }
                else
                    NavigateApp("credit");
                break;
            case "fund_tax_vault":
                NavigateApp("taxvault");
                break;
            case "set_books_from_bank":
                if (_monthCloseAccountId is int mca)
                    _booksAccountId = mca;
                await TrustBooksFromBankAsync();
                break;
            case "reconcile":
            case "import":
                NavigateApp("import");
                break;
            case "backup":
                NavigateApp("data");
                break;
            case "mark_closed":
                await MarkMonthClosed_Click_Core();
                break;
            default:
                Done_Click(sender, e);
                break;
        }
    }

    private void BooksSecondary_Click(object sender, RoutedEventArgs e)
    {
        if (_booksSecondaryAction is "review")
            NavigateApp("review");
        else if (_booksSecondaryAction is "import")
            NavigateApp("import");
        else
            Frame?.Navigate(typeof(ReviewPage));
    }

    private async void MarkMonthClosed_Click(object sender, RoutedEventArgs e)
        => await MarkMonthClosed_Click_Core();

    private async Task MarkMonthClosed_Click_Core()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.MarkMonthClosedAsync();
            var msg = JsonUi.Str(res, "message", "Month marked closed.");
            await RefreshAsync();
            ShowSuccess("Month closed", msg);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = FriendlyLoadError(ex);
            ErrorBar.IsOpen = true;
        }
    }

    private async Task Promo_Click_Core(int accountId)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CreatePromoSinkBillAsync(accountId);
            var msg =
                $"Set-aside ready · {JsonUi.Str(res, "name")} · ${JsonUi.Str(res, "monthly_sink")}/mo";
            await RefreshAsync();
            ShowSuccess("0% set-aside", msg);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = FriendlyLoadError(ex);
            ErrorBar.IsOpen = true;
        }
    }

    private void TaxYear_Click(object sender, RoutedEventArgs e)
    {
        switch (_taxYearAction)
        {
            case "review":
                Frame?.Navigate(typeof(ReviewPage));
                break;
            case "fund_tax_vault":
                Frame?.Navigate(typeof(TaxVaultPage));
                break;
            case "tax_packet":
                Frame?.Navigate(typeof(TaxPage));
                break;
            default:
                Frame?.Navigate(typeof(TaxPage));
                break;
        }
    }

    private async void DoNext_Click(object sender, RoutedEventArgs e)
    {
        switch (_nextAction)
        {
            case "rescue":
            case "protect_checking":
                Rescue_Click(sender, e);
                break;
            case "fees":
            case "stop_fees":
                // Prefer fee card on Home when present; else Sort charges
                if (FeeCard is not null && FeeCard.Visibility == Visibility.Visible)
                {
                    FeeCard.StartBringIntoView();
                    BriefText.Text = NextReason.Text
                        + "\n\nConfirm fee-like charges so they stop hitting Safe to spend.";
                }
                else
                    NavigateApp("review");
                break;
            case "plaid":
                NavigateApp("plaid");
                break;
            case "import":
                NavigateApp("import");
                break;
            case "set_books_from_bank":
                await TrustBooksFromBankAsync();
                break;
            case "promo_sink":
            case "promo_balloon":
                if (_promoAccountId is int promoId)
                    await Promo_Click_Core(promoId);
                else if (_home.TryGetProperty("do_this_next", out var pn)
                         && pn.ValueKind == JsonValueKind.Object
                         && pn.TryGetProperty("params", out var pp)
                         && pp.ValueKind == JsonValueKind.Object)
                {
                    var aid = JsonUi.Int(pp, "account_id", 0);
                    if (aid > 0)
                        await Promo_Click_Core(aid);
                    else
                        NavigateApp("credit");
                }
                else
                    NavigateApp("credit");
                break;
            case "review":
            case "uncategorized":
                NavigateApp("review");
                break;
            case "ledger":
            case "pending_txns":
                NavigateApp("ledger");
                break;
            case "attack_apr":
                NavigateApp("credit");
                break;
            case "wealth_401k_match":
            case "wealth_ira":
            case "wealth_529":
            case "wealth_iul_edu":
                BriefText.Text = NextReason.Text + "\n\n" + UiCopy.WealthDisclaimer;
                break;
            case "fund_tax_vault":
            case "respect_tax_vault":
                NavigateApp("taxvault");
                break;
            case "top_up_buffer":
                NavigateApp("settings");
                break;
            case "add_bill":
                Frame?.Navigate(typeof(MoneyWizardPage), "bill");
                break;
            default:
                Done_Click(sender, e);
                break;
        }
    }

    private void Add_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(AddHubPage));
    private void Buy_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(BuyPage));
    private void Setup_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(FirstRunPage));
    private void EmptyBill_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(MoneyWizardPage), "bill");

    private void BankTip_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Windows.Storage.ApplicationData.Current.LocalSettings.Values["BankTipDismissed"] = true;
        }
        catch { /* ignore */ }
        BankTipBar.IsOpen = false;
        Frame?.Navigate(typeof(PlaidPage));
    }

    private async void StartEngine_Click(object sender, RoutedEventArgs e)
    {
        EngineBar.IsOpen = false;
        if (App.Backend is null)
        {
            EngineBar.Message = "Engine host not available — reinstall or run from package folder.";
            EngineBar.IsOpen = true;
            return;
        }
        var ok = await App.Backend.EnsureRunningAsync();
        if (ok)
            await RefreshAsync();
        else
        {
            EngineBar.Message = App.Backend.LastError
                ?? "Still offline. Settings → Start engine, or keep engine\\ next to the EXE.";
            EngineBar.IsOpen = true;
        }
    }

    private void Done_Click(object sender, RoutedEventArgs e)
    {
        DoneText.Text = $"Done · {DateTime.Now:t} — open rarely. 3-minute check when you're back.";
        try
        {
            Windows.Storage.ApplicationData.Current.LocalSettings.Values["BankTipDismissed"] = true;
        }
        catch { /* ignore */ }
        BankTipBar.IsOpen = false;
    }

    private async void Rescue_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.LiquidityRescueAsync(amount: 500m);
            BriefText.Text = JsonUi.Str(res, "message");
            var lines = new List<string>();
            if (res.TryGetProperty("options", out var opts) && opts.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in opts.EnumerateArray())
                {
                    var safe = o.TryGetProperty("safe", out var s) && s.ValueKind == JsonValueKind.True ? "✓" : "!";
                    lines.Add($"{safe} {JsonUi.Str(o, "title")} — {JsonUi.Str(o, "reason")}");
                }
            }
            RescueList.ItemsSource = lines.Count > 0 ? lines : new List<string> { "No options." };
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Brief_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.GetDigestBriefAsync(true);
            BriefText.Text = $"[{JsonUi.Str(res, "source")}] {JsonUi.Str(res, "brief")}";
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
