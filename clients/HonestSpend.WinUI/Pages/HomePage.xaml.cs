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
    private int? _promoAccountId;
    private string _monthCloseAction = "hold";
    private int? _monthCloseAccountId;
    private string _taxYearAction = "hold";
    private readonly List<(int TxnId, string Label)> _feeItems = new();
    private int _feeIdx;
    private readonly List<JsonElement> _recurringItems = new();
    private int _recurringIdx;

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
                        if (it.TryGetProperty("transaction_id", out var tid) && tid.ValueKind == JsonValueKind.Number)
                            _feeItems.Add((tid.GetInt32(), JsonUi.Str(it, "label", JsonUi.Str(it, "payee"))));
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

            // Promo set-aside (H1-C3)
            _promoAccountId = null;
            if (_home.TryGetProperty("promo_brief", out var promo) && promo.ValueKind == JsonValueKind.Object
                && promo.TryGetProperty("needs_attention", out var pna) && pna.ValueKind == JsonValueKind.True)
            {
                PromoCard.Visibility = Visibility.Visible;
                PromoTitle.Text = JsonUi.Str(promo, "title");
                PromoReason.Text = JsonUi.Str(promo, "reason");
                PromoBtn.Content = JsonUi.Str(promo, "button_label", "Create set-aside");
                PromoMsg.Text = "";
                if (promo.TryGetProperty("account_id", out var paid) && paid.ValueKind == JsonValueKind.Number)
                    _promoAccountId = paid.GetInt32();
            }
            else
            {
                PromoCard.Visibility = Visibility.Collapsed;
            }

            // Month-close (H1-B)
            _monthCloseAction = "hold";
            _monthCloseAccountId = null;
            if (_home.TryGetProperty("month_close", out var mc) && mc.ValueKind == JsonValueKind.Object)
            {
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
                            if (st.TryGetProperty("account_id", out var maid) && maid.ValueKind == JsonValueKind.Number)
                                _monthCloseAccountId = maid.GetInt32();
                        }
                    }
                }
                MonthCloseList.ItemsSource = mLines;
                var allDone = mc.TryGetProperty("all_done", out var mad) && mad.ValueKind == JsonValueKind.True;
                MonthCloseBtn.Visibility = allDone ? Visibility.Collapsed : Visibility.Visible;
            }

            // Tax year prep (H2-B)
            _taxYearAction = "hold";
            if (_home.TryGetProperty("tax_year", out var ty) && ty.ValueKind == JsonValueKind.Object)
            {
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
                var tDone = ty.TryGetProperty("all_done", out var tad) && tad.ValueKind == JsonValueKind.True;
                TaxYearBtn.Visibility = tDone ? Visibility.Collapsed : Visibility.Visible;
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
        RecurringItemLabel.Text =
            $"{_recurringIdx + 1}/{_recurringItems.Count}: {JsonUi.Str(s, "name")} · " +
            $"${JsonUi.Str(s, "amount_abs")}/{JsonUi.Str(s, "cadence")}";
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
            var body = new Dictionary<string, object?>
            {
                ["name"] = JsonUi.Str(s, "name"),
                ["amount"] = decimal.TryParse(JsonUi.Str(s, "amount_abs"), System.Globalization.NumberStyles.Any, System.Globalization.CultureInfo.InvariantCulture, out var a) ? a : 0m,
                ["cadence"] = JsonUi.Str(s, "cadence", "monthly"),
                ["next_date"] = string.IsNullOrEmpty(nextRaw) || nextRaw == "—" ? null : nextRaw,
                ["profile_id"] = AppState.SelectedProfileId,
            };
            var res = await api.AcceptRecurringAsync(body);
            RecurringMsg.Text = $"Added · {JsonUi.Str(res, "name")} · {JsonUi.Str(res, "cadence")}";
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
            PromoMsg.Text =
                $"Set-aside ready · {JsonUi.Str(res, "name")} · ${JsonUi.Str(res, "monthly_sink")}/mo";
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void MonthClose_Click(object sender, RoutedEventArgs e)
    {
        switch (_monthCloseAction)
        {
            case "review":
                Frame?.Navigate(typeof(ReviewPage));
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
                    Frame?.Navigate(typeof(CreditPage));
                break;
            case "fund_tax_vault":
                Frame?.Navigate(typeof(TaxVaultPage));
                break;
            case "reconcile":
                Frame?.Navigate(typeof(ReconcilePage));
                break;
            case "backup":
                Frame?.Navigate(typeof(DataPage));
                break;
            default:
                Done_Click(sender, e);
                break;
        }
    }

    private async Task Promo_Click_Core(int accountId)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreatePromoSinkBillAsync(accountId);
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
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
                // Fee candidates live on Full books; Review still useful — show brief text
                BriefText.Text =
                    NextReason.Text
                    + "\n\nTip: Full books → Import history or re-scan after Sort charges. "
                    + "Confirm fee-like payees so they stop hitting Safe to spend.";
                Frame?.Navigate(typeof(ReviewPage));
                break;
            case "plaid":
                Frame?.Navigate(typeof(PlaidPage));
                break;
            case "import":
                Frame?.Navigate(typeof(ImportPage));
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
