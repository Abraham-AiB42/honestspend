using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

/// <summary>
/// Smart setup wizard shell (PR1): welcome → path (Plaid / CSV / manual) →
/// manual short path or placeholders for later phases. Resume via setup_phase.
/// </summary>
public sealed partial class FirstRunPage : Page
{
    private string _phase = "welcome";
    private string? _path;
    private int _progress;
    private bool _loading;

    // Manual path local steps (legacy first-run)
    private int _manualStep;
    private const int ManualMax = 6;

    private TextBox? _cashName;
    private TextBox? _inst;
    private NumberBox? _cashBal;
    private NumberBox? _buffer;
    private CheckBox? _wantCard;
    private TextBox? _cardName;
    private NumberBox? _cardBal;
    private NumberBox? _cardLimit;
    private NumberBox? _cardDue;
    private CheckBox? _wantBill;
    private TextBox? _billName;
    private NumberBox? _billAmt;
    private CalendarDatePicker? _billNext;
    private ComboBox? _importCadenceBox;
    private ComboBox? _importFocusBox;

    private string _cashNameV = "Primary checking";
    private string? _instV;
    private decimal _cashBalV;
    private decimal _bufferV = 1000;
    private bool _wantCardV;
    private string _cardNameV = "Everyday card";
    private decimal _cardBalV;
    private decimal _cardLimitV = 5000;
    private int _cardDueV = 15;
    private bool _wantBillV;
    private string _billNameV = "Housing / rent";
    private decimal _billAmtV = 1500;
    private DateTimeOffset _billNextV = DateTimeOffset.Now.AddDays(14);
    private string _importCadenceV = "weekly";
    private string _importFocusV = "transactions";

    public FirstRunPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await RefreshStateAsync();
    }

    private async Task RefreshStateAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.GetSetupStateAsync();
            ApplyState(st);
            Render();
        }
        catch (Exception ex)
        {
            // Offline: fall back to local welcome
            _phase = "welcome";
            StepLabel.Text = "Engine starting…";
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            Render();
        }
    }

    private void ApplyState(JsonElement st)
    {
        _phase = JsonUi.Str(st, "phase", "welcome");
        _path = st.TryGetProperty("path", out var p) && p.ValueKind == JsonValueKind.String
            ? p.GetString()
            : null;
        _progress = 0;
        if (st.TryGetProperty("progress_pct", out var pct) && pct.TryGetInt32(out var n))
            _progress = n;
        ProgressBar.Value = _progress;
        if (_phase == "manual" && _manualStep == 0)
            _manualStep = 0;
        if (_phase == "done")
        {
            AppState.ShowSetupNav = false;
        }
    }

    private void Render()
    {
        ErrorBar.IsOpen = false;
        InfoBar.IsOpen = false;
        Fields.Children.Clear();
        BackBtn.IsEnabled = _phase is not ("welcome" or "done");
        NextBtn.Content = "Next";
        NextBtn.IsEnabled = true;
        ProgressBar.Value = _progress;
        StepLabel.Text = $"{_phase.Replace('_', ' ')} · {_progress}%";

        if (_phase == "done")
        {
            QuestionText.Text = "You're set";
            HintText.Text = "Open Home for Safe to spend and Do this next.";
            NextBtn.Content = "Go to Home";
            BackBtn.IsEnabled = false;
            return;
        }

        if (_phase == "welcome")
        {
            QuestionText.Text = "We'll answer one question";
            HintText.Text =
                "What can you safely spend without bouncing checking or paying dumb interest?\n\n" +
                "About 15–40 minutes if you import bank history — or 2 minutes for a quick manual start. " +
                "You can pause anytime; we remember where you left off.";
            NextBtn.Content = "Let's go";
            return;
        }

        if (_phase == "path")
        {
            QuestionText.Text = "How do you want to connect money?";
            HintText.Text =
                "Plaid uses your own free trial keys (up to 10 bank connections). " +
                "CSV never needs bank passwords inside HonestSpend. " +
                "Quick manual is fastest if you're in a hurry.";

            void addPath(string id, string title, string detail)
            {
                var btn = new Button
                {
                    Content = new StackPanel
                    {
                        Spacing = 2,
                        Children =
                        {
                            new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold },
                            new TextBlock { Text = detail, Opacity = 0.75, TextWrapping = TextWrapping.Wrap, FontSize = 12 },
                        },
                    },
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Left,
                    Padding = new Thickness(12),
                    Tag = id,
                    Margin = new Thickness(0, 0, 0, 4),
                };
                btn.Click += async (_, _) => await ChoosePathAsync(id);
                Fields.Children.Add(btn);
            }

            addPath("plaid", "Plaid (your keys)", "Signup → paste client id + secret → Link banks. Best for ongoing sync.");
            addPath("csv", "CSV / OFX imports", "Add cash accounts one-by-one with bank download guides. Free forever.");
            addPath("manual", "Quick manual (2 min)", "One checking, optional card & bill. Import later.");
            NextBtn.IsEnabled = false;
            NextBtn.Content = "Pick a path above";
            return;
        }

        if (_phase == "manual")
        {
            RenderManual();
            return;
        }

        // Placeholder phases until PR2+
        var (title, hint, nextLabel) = _phase switch
        {
            "plaid_keys" => (
                "Plaid keys (next update)",
                "You'll enter client id + secret here with secure local storage, then Link. " +
                "For now: Settings/env, or pick CSV / Quick manual. Skip advances the wizard.",
                "Continue (placeholder)"),
            "plaid_link" => (
                "Link banks (next update)",
                "App-owned Plaid Link — up to 10 institutions on trial. Placeholder; Next continues.",
                "Continue"),
            "ai_keys" => (
                "AI helpers (optional)",
                "After Plaid: connect Grok or another LLM you prefer (BYOK, local only). Skip for now.",
                "Skip AI for now"),
            "cash_loop" => (
                "Cash accounts (next update)",
                "Pattern: + Cash account → type → bank → import. Placeholder; use Quick manual or wait for next build.",
                "Continue"),
            "import_cash" => (
                "Import cash history",
                "Bank guides + CSV import per account. Coming next — you can use Full books → Import now.",
                "Continue"),
            "discover" => (
                "Find cards & bills",
                "We'll skim debits for card payments, loans, and recurring investments. Coming soon.",
                "Continue"),
            "liabilities" => (
                "Set up debts",
                "Cards get payment options: minimum, fixed, statement, interest-saving. Coming soon.",
                "Continue"),
            "recurring" => (
                "Recurring bills",
                "Accept detected bills and recurring investments. Coming soon.",
                "Continue"),
            "categorize" => (
                "Categories",
                "Auto-categorize + confirm top ambiguous payees. Coming soon.",
                "Continue"),
            "budgets" => (
                "Budgets",
                "Seed from history and review amounts. Coming soon.",
                "Continue"),
            "buffers" => (
                "Safety buffers",
                "Per-account buffer + total cash floor. Coming soon.",
                "Finish setup"),
            _ => (
                _phase.Replace('_', ' '),
                "Continue setup or skip for now.",
                "Next"),
        };
        QuestionText.Text = title;
        HintText.Text = hint;
        NextBtn.Content = nextLabel;
        InfoBar.Title = "Wizard foundation";
        InfoBar.Message = "Path saved. Full steps ship in the next builds — you can still skip to Home.";
        InfoBar.IsOpen = true;
    }

    private void RenderManual()
    {
        StepLabel.Text = $"Quick manual · step {_manualStep + 1} of {ManualMax + 1}";
        NextBtn.Content = _manualStep >= ManualMax ? "Finish" : "Next";
        BackBtn.IsEnabled = _manualStep > 0 || _phase != "welcome";

        switch (_manualStep)
        {
            case 0:
                QuestionText.Text = "Your primary checking";
                HintText.Text = "Rainy-day floor stays out of Safe to spend (default $1,000 total buffer for now).";
                _cashName = new TextBox { Header = "Nickname", Text = _cashNameV };
                _inst = new TextBox { Header = "Bank (optional)", Text = _instV ?? "" };
                _cashBal = new NumberBox { Header = "Balance today ($)", Value = (double)_cashBalV, Minimum = 0 };
                _buffer = new NumberBox { Header = "Total rainy-day floor ($)", Value = (double)_bufferV, Minimum = 0 };
                Fields.Children.Add(_cashName);
                Fields.Children.Add(_inst);
                Fields.Children.Add(_cashBal);
                Fields.Children.Add(_buffer);
                break;
            case 1:
                QuestionText.Text = "Add a credit card now?";
                HintText.Text = "Optional. Due day helps interest-free planning.";
                _wantCard = new CheckBox { Content = "Yes — add a card", IsChecked = _wantCardV };
                _wantCard.Checked += (_, _) => CardFields(true);
                _wantCard.Unchecked += (_, _) => CardFields(false);
                Fields.Children.Add(_wantCard);
                CardFields(_wantCardV);
                break;
            case 2:
                QuestionText.Text = "Biggest monthly bill?";
                HintText.Text = "Optional. Makes Safe to spend realistic on day one.";
                _wantBill = new CheckBox { Content = "Yes — add one bill", IsChecked = _wantBillV };
                _wantBill.Checked += (_, _) => BillFields(true);
                _wantBill.Unchecked += (_, _) => BillFields(false);
                Fields.Children.Add(_wantBill);
                BillFields(_wantBillV);
                break;
            case 3:
                QuestionText.Text = "How often should we remind you to refresh from your bank?";
                HintText.Text = "Download CSV/OFX yourself — we never store bank passwords.";
                _importCadenceBox = new ComboBox { Header = "Reminder cadence", HorizontalAlignment = HorizontalAlignment.Stretch };
                AddCombo(_importCadenceBox, "off", "Off", _importCadenceV == "off");
                AddCombo(_importCadenceBox, "daily", "Daily", _importCadenceV == "daily");
                AddCombo(_importCadenceBox, "weekly", "Weekly (default)", _importCadenceV is "weekly" or null or "");
                AddCombo(_importCadenceBox, "monthly", "Monthly", _importCadenceV == "monthly");
                _importFocusBox = new ComboBox { Header = "What to download", HorizontalAlignment = HorizontalAlignment.Stretch };
                AddCombo(_importFocusBox, "transactions", "Transactions CSV/OFX", _importFocusV is "transactions" or null or "");
                AddCombo(_importFocusBox, "statements", "Statements", _importFocusV == "statements");
                AddCombo(_importFocusBox, "both", "Both", _importFocusV == "both");
                Fields.Children.Add(_importCadenceBox);
                Fields.Children.Add(_importFocusBox);
                break;
            case 4:
                QuestionText.Text = "Review";
                HintText.Text = "We'll create these and mark setup complete.";
                var lines = new List<string>
                {
                    $"Checking: {_cashNameV} · {_cashBalV:C}",
                    $"Total buffer: {_bufferV:C}",
                };
                if (_wantCardV)
                    lines.Add($"Card: {_cardNameV} · owed {_cardBalV:C} · due day {_cardDueV}");
                if (_wantBillV)
                    lines.Add($"Bill: {_billNameV} · {_billAmtV:C}/mo");
                Fields.Children.Add(new ItemsControl { ItemsSource = lines });
                break;
            default:
                QuestionText.Text = "You're set";
                HintText.Text = MsgText.Text.Length > 0 ? MsgText.Text : "Open Home for Safe to spend.";
                NextBtn.Content = "Go to Home";
                break;
        }
    }

    private static void AddCombo(ComboBox box, string tag, string label, bool selected)
    {
        var item = new ComboBoxItem { Content = label, Tag = tag };
        box.Items.Add(item);
        if (selected) box.SelectedItem = item;
        if (box.SelectedItem is null && box.Items.Count == 1)
            box.SelectedIndex = 0;
    }

    private void CardFields(bool show)
    {
        while (Fields.Children.Count > 1)
            Fields.Children.RemoveAt(Fields.Children.Count - 1);
        if (!show) return;
        _cardName = new TextBox { Header = "Card nickname", Text = _cardNameV };
        _cardBal = new NumberBox { Header = "Balance owed ($)", Value = (double)_cardBalV, Minimum = 0 };
        _cardLimit = new NumberBox { Header = "Credit limit ($)", Value = (double)_cardLimitV, Minimum = 0 };
        _cardDue = new NumberBox { Header = "Payment due day (1–31)", Value = _cardDueV, Minimum = 1, Maximum = 31 };
        Fields.Children.Add(_cardName);
        Fields.Children.Add(_cardBal);
        Fields.Children.Add(_cardLimit);
        Fields.Children.Add(_cardDue);
    }

    private void BillFields(bool show)
    {
        while (Fields.Children.Count > 1)
            Fields.Children.RemoveAt(Fields.Children.Count - 1);
        if (!show) return;
        _billName = new TextBox { Header = "Bill name", Text = _billNameV };
        _billAmt = new NumberBox { Header = "Amount ($)", Value = (double)_billAmtV, Minimum = 0.01 };
        _billNext = new CalendarDatePicker { Header = "Next due", Date = _billNextV };
        Fields.Children.Add(_billName);
        Fields.Children.Add(_billAmt);
        Fields.Children.Add(_billNext);
    }

    private void CaptureManual()
    {
        if (_cashName is not null) _cashNameV = _cashName.Text?.Trim() ?? "Primary checking";
        if (_inst is not null) _instV = string.IsNullOrWhiteSpace(_inst.Text) ? null : _inst.Text.Trim();
        if (_cashBal is not null && !double.IsNaN(_cashBal.Value)) _cashBalV = (decimal)_cashBal.Value;
        if (_buffer is not null && !double.IsNaN(_buffer.Value)) _bufferV = (decimal)_buffer.Value;
        if (_wantCard is not null) _wantCardV = _wantCard.IsChecked == true;
        if (_cardName is not null) _cardNameV = _cardName.Text?.Trim() ?? "Card";
        if (_cardBal is not null && !double.IsNaN(_cardBal.Value)) _cardBalV = (decimal)_cardBal.Value;
        if (_cardLimit is not null && !double.IsNaN(_cardLimit.Value)) _cardLimitV = (decimal)_cardLimit.Value;
        if (_cardDue is not null && !double.IsNaN(_cardDue.Value)) _cardDueV = (int)_cardDue.Value;
        if (_wantBill is not null) _wantBillV = _wantBill.IsChecked == true;
        if (_billName is not null) _billNameV = _billName.Text?.Trim() ?? "Bill";
        if (_billAmt is not null && !double.IsNaN(_billAmt.Value)) _billAmtV = (decimal)_billAmt.Value;
        if (_billNext?.Date is not null) _billNextV = _billNext.Date.Value;
        if (_importCadenceBox?.SelectedItem is ComboBoxItem ci && ci.Tag is string cad)
            _importCadenceV = cad;
        if (_importFocusBox?.SelectedItem is ComboBoxItem fi && fi.Tag is string foc)
            _importFocusV = foc;
    }

    private async Task ChoosePathAsync(string path)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.SetupAdvanceAsync("set_path", path: path);
            ApplyState(st);
            _manualStep = 0;
            Render();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Back_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (_phase == "manual" && _manualStep > 0)
            {
                CaptureManual();
                _manualStep--;
                Render();
                return;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.SetupAdvanceAsync("back");
            ApplyState(st);
            Render();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Next_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        if (_loading) return;
        try
        {
            _loading = true;
            if (_phase == "done")
            {
                Frame?.Navigate(typeof(HomePage));
                return;
            }

            if (_phase == "path")
                return; // must pick a button

            if (_phase == "manual")
            {
                await ManualNextAsync();
                return;
            }

            if (_phase == "welcome")
            {
                using var api = new LedgerApiClient();
                await api.EnsureBackendAsync();
                var st = await api.SetupAdvanceAsync("next");
                ApplyState(st);
                Render();
                return;
            }

            // Placeholder phases: advance server state
            using (var api = new LedgerApiClient())
            {
                await api.EnsureBackendAsync();
                var st = await api.SetupAdvanceAsync("next");
                ApplyState(st);
                if (_phase == "done")
                    AppState.ShowSetupNav = false;
                Render();
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
        finally
        {
            _loading = false;
        }
    }

    private async Task ManualNextAsync()
    {
        CaptureManual();
        if (_manualStep == 0 && string.IsNullOrWhiteSpace(_cashNameV))
            throw new InvalidOperationException("Name your checking account.");
        if (_manualStep == 1 && _wantCardV && (_cardDueV < 1 || _cardDueV > 31))
            throw new InvalidOperationException("Card needs a payment due day (1–31).");
        if (_manualStep == 2 && _wantBillV && _billAmtV <= 0)
            throw new InvalidOperationException("Bill amount must be greater than zero.");

        if (_manualStep < 4)
        {
            _manualStep++;
            Render();
            return;
        }

        if (_manualStep == 4)
        {
            await SubmitManualAsync();
            _manualStep = 5;
            Render();
            return;
        }

        Frame?.Navigate(typeof(HomePage));
    }

    private async Task SubmitManualAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var body = new Dictionary<string, object?>
        {
            ["profile_slug"] = "personal",
            ["cash_name"] = _cashNameV,
            ["cash_balance"] = _cashBalV,
            ["cash_institution"] = _instV,
            ["safety_buffer"] = _bufferV,
            ["ifpp_mode"] = "conservative",
            ["import_reminder_cadence"] = _importCadenceV,
            ["import_reminder_focus"] = _importFocusV,
        };
        if (_wantCardV)
        {
            body["card_name"] = _cardNameV;
            body["card_balance"] = _cardBalV;
            body["card_limit"] = _cardLimitV;
            body["card_due_day"] = _cardDueV;
        }
        if (_wantBillV)
        {
            body["bill_name"] = _billNameV;
            body["bill_amount"] = _billAmtV;
            body["bill_next_date"] = _billNextV.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        var res = await api.FirstRunAsync(body);
        MsgText.Text = JsonUi.Str(res, "message");
        if (string.IsNullOrEmpty(MsgText.Text) || MsgText.Text == "—")
            MsgText.Text = "Accounts created. Safe to spend is ready on Home.";
        // first-run marks setup done on server
        var st = await api.GetSetupStateAsync();
        ApplyState(st);
        AppState.ShowSetupNav = false;
    }

    private async void Skip_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.SetupCompleteAsync("skipped-from-wizard");
            AppState.ShowSetupNav = false;
            Frame?.Navigate(typeof(HomePage));
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
