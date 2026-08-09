using System.Globalization;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using Windows.UI;

namespace LedgerRing_WinUI.Pages;

public sealed partial class HomePage : Page
{
    private JsonElement _home;
    private string _nextAction = "hold";
    private string _ritualNextAction = "hold";
    private string _booksAction = "hold";

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
            _home = await api.GetHomeSimpleAsync();

            SafeText.Text = Money(_home, "safe_to_spend");
            var status = JsonUi.Str(_home, "status", "safe");
            StatusLine.Text = JsonUi.Str(_home, "status_label", status);
            StatusLine.Foreground = status switch
            {
                "danger" => new SolidColorBrush(Color.FromArgb(255, 255, 100, 100)),
                "watch" => new SolidColorBrush(Color.FromArgb(255, 255, 180, 60)),
                _ => new SolidColorBrush(Color.FromArgb(255, 80, 200, 120)),
            };

            var risk = JsonUi.Str(_home, "next_risk_day", "");
            RiskLine.Text = string.IsNullOrEmpty(risk) || risk == "—"
                ? "No near-term red day"
                : $"{UiCopy.NextRisk}: {risk}";

            var pend = JsonUi.Str(_home, "pending_warning", "");
            PendingLine.Text = (string.IsNullOrEmpty(pend) || pend == "—") ? "" : pend;
            PendingLine.Visibility = string.IsNullOrEmpty(PendingLine.Text)
                ? Visibility.Collapsed
                : Visibility.Visible;

            WhoLine.Text =
                $"{JsonUi.Str(_home, "who_name")} · " +
                (JsonUi.Str(_home, "money_view") == "all_money" ? UiCopy.AllMoney : UiCopy.ThisMoney);

            if (_home.TryGetProperty("do_this_next", out var next) && next.ValueKind == JsonValueKind.Object)
            {
                NextTitle.Text = JsonUi.Str(next, "title");
                NextReason.Text = JsonUi.Str(next, "reason");
                NextBtn.Content = JsonUi.Str(next, "button_label", "Continue");
                _nextAction = JsonUi.Str(next, "action", "hold");
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
                    alerts.Add($"[{JsonUi.Str(a, "level")}] {JsonUi.Str(a, "title")}");
            }
            if (alerts.Count == 0) alerts.Add("All clear — no action queue.");
            AlertList.ItemsSource = alerts;

            // Live books / import brief (dream H1-A1)
            _booksAction = "hold";
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

            // 3-minute open-rarely ritual
            _ritualNextAction = "hold";
            if (_home.TryGetProperty("three_minute_check", out var ritual) && ritual.ValueKind == JsonValueKind.Object)
            {
                RitualSubtitle.Text = JsonUi.Str(ritual, "subtitle", "Open rarely — tick these and close.");
                RitualProgress.Text = JsonUi.Str(ritual, "progress_label", "");
                var rLines = new List<string>();
                if (ritual.TryGetProperty("steps", out var rs) && rs.ValueKind == JsonValueKind.Array)
                {
                    foreach (var st in rs.EnumerateArray())
                    {
                        var done = st.TryGetProperty("done", out var d) && d.ValueKind == JsonValueKind.True;
                        rLines.Add($"{(done ? "✓" : "○")} {JsonUi.Str(st, "title")} — {JsonUi.Str(st, "detail")}");
                        if (!done && _ritualNextAction == "hold")
                            _ritualNextAction = JsonUi.Str(st, "action", "hold");
                    }
                }
                RitualList.ItemsSource = rLines;
                var allDone = ritual.TryGetProperty("all_done", out var ad) && ad.ValueKind == JsonValueKind.True;
                RitualNextBtn.Visibility = allDone ? Visibility.Collapsed : Visibility.Visible;
                RitualNextBtn.Content = allDone ? "All clear" : "Do next open item";
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

            StatusText.Text = $"Connected · {JsonUi.Str(_home, "as_of")}";
        }
        catch (Exception ex)
        {
            StatusText.Text = "Error";
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

    private void Books_Click(object sender, RoutedEventArgs e)
    {
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

    private void DoNext_Click(object sender, RoutedEventArgs e)
    {
        switch (_nextAction)
        {
            case "rescue":
            case "protect_checking":
                Rescue_Click(sender, e);
                break;
            case "fees":
            case "stop_fees":
                Frame?.Navigate(typeof(ReviewPage));
                break;
            case "promo_sink":
            case "promo_balloon":
                Frame?.Navigate(typeof(CreditPage));
                break;
            case "review":
            case "uncategorized":
                Frame?.Navigate(typeof(ReviewPage));
                break;
            case "ledger":
            case "pending_txns":
                Frame?.Navigate(typeof(LedgerPage));
                break;
            case "attack_apr":
                Frame?.Navigate(typeof(CreditPage));
                break;
            case "wealth_401k_match":
            case "wealth_ira":
            case "wealth_529":
            case "wealth_iul_edu":
                BriefText.Text = NextReason.Text + "\n\n" + UiCopy.WealthDisclaimer;
                break;
            case "fund_tax_vault":
            case "respect_tax_vault":
                BriefText.Text =
                    NextReason.Text
                    + "\n\nTax set-aside keeps Safe to spend honest. Full books → Tax vault to adjust.";
                break;
            case "top_up_buffer":
                Frame?.Navigate(typeof(SettingsPage));
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
