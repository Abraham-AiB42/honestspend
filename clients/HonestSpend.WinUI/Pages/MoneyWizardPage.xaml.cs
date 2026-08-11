using System.Linq;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

/// <summary>Step wizards for cash, savings, card, loan, bill, income, owner_draw, business, child.</summary>
public sealed partial class MoneyWizardPage : Page
{
    private string _kind = "cash";
    private int _step;
    private List<JsonElement> _profiles = new();
    private List<JsonElement> _accounts = new();

    // shared fields
    private ComboBox? _whoBox;
    private TextBox? _nameBox;
    private TextBox? _instBox;
    private NumberBox? _balBox;
    private NumberBox? _limitBox;
    private NumberBox? _aprBox;
    private NumberBox? _minBox;
    private NumberBox? _dueBox;
    private NumberBox? _apyBox;
    private NumberBox? _amtBox;
    private ComboBox? _cadenceBox;
    private ComboBox? _accountBox;
    private ComboBox? _taxBox;
    private CalendarDatePicker? _nextDateBox;
    private CalendarDatePicker? _startDateBox;
    private CalendarDatePicker? _endDateBox;
    private CalendarDatePicker? _promoEndBox;
    private CheckBox? _promoBox;
    private ComboBox? _autopayBox;
    private CheckBox? _certaintyBox;
    private TextBox? _vendorBox;
    private TextBox? _incomeSourceBox;
    private ComboBox? _opexBox;
    private RewardsRatesUi? _rewardsUi;
    private Dictionary<string, decimal>? _rewardsRates;

    public MoneyWizardPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        _kind = e.Parameter as string ?? "cash";
        _step = 0;
        TitleText.Text = _kind switch
        {
            "cash" => "Add checking / cash",
            "savings" => "Add savings",
            "card" => "Add credit card",
            "loan" => "Add loan",
            "bill" => "Add bill",
            "income" => "Add income",
            "owner_draw" => "Add owner draw",
            "business" => "Add business",
            "child" => "Add child",
            _ => "Add",
        };
        await LoadContextAsync();
        RenderStep();
    }

    private async Task LoadContextAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var p = await api.GetProfilesAsync();
            _profiles = p.EnumerateArray().ToList();
            var a = await api.GetAccountsAsync();
            _accounts = a.EnumerateArray().ToList();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private int MaxStep => _kind switch
    {
        "cash" or "savings" => 3,
        "card" => 5,
        "loan" => 4,
        "bill" or "income" or "owner_draw" => 5,
        "business" or "child" => 2,
        _ => 2,
    };

    private bool IsScheduleKind => _kind is "bill" or "income" or "owner_draw";

    private void RenderStep()
    {
        ErrorBar.IsOpen = false;
        FieldsPanel.Children.Clear();
        HintText.Text = "";
        StepText.Text = $"Step {_step + 1} of {MaxStep + 1}";
        BackBtn.IsEnabled = _step > 0;
        NextBtn.Content = _step >= MaxStep ? "Finish" : "Next";

        switch (_kind)
        {
            case "cash":
            case "savings":
                RenderCashSteps();
                break;
            case "card":
                RenderCardSteps();
                break;
            case "loan":
                RenderLoanSteps();
                break;
            case "bill":
            case "income":
            case "owner_draw":
                RenderBillSteps();
                break;
            case "business":
            case "child":
                RenderEntitySteps();
                break;
        }
    }

    private void RenderCashSteps()
    {
        if (_step == 0)
        {
            QuestionText.Text = "Whose money is this?";
            HintText.Text = "Usually Personal. Businesses and kids are separate piles.";
            _whoBox = WhoCombo();
            FieldsPanel.Children.Add(_whoBox);
        }
        else if (_step == 1)
        {
            QuestionText.Text = _kind == "savings" ? "Name this savings account" : "Name this checking account";
            HintText.Text = "Something you'll recognize — e.g. Primary checking.";
            _nameBox = new TextBox { Header = "Nickname", Text = _kind == "savings" ? "High-yield savings" : "Primary checking" };
            _instBox = new TextBox { Header = "Bank (optional)", PlaceholderText = "Bank name" };
            FieldsPanel.Children.Add(_nameBox);
            FieldsPanel.Children.Add(_instBox);
        }
        else if (_step == 2)
        {
            QuestionText.Text = "What's the balance today?";
            HintText.Text = "Rough is fine — you can refine later.";
            _balBox = new NumberBox { Header = "Balance ($)", Value = 0, Minimum = 0 };
            FieldsPanel.Children.Add(_balBox);
            if (_kind == "savings")
            {
                _apyBox = new NumberBox { Header = "APY if you know it (e.g. 0.045 for 4.5%)", Minimum = 0, Maximum = 1, SmallChange = 0.001 };
                FieldsPanel.Children.Add(_apyBox);
                HintText.Text += " APY helps us compare cash yield vs paying cheap debt.";
            }
        }
        else
        {
            QuestionText.Text = "Ready to add?";
            HintText.Text = "We'll include this in Safe to spend when appropriate.";
        }
    }

    private void RenderCardSteps()
    {
        if (_step == 0)
        {
            QuestionText.Text = "Whose card is this?";
            HintText.Text = "Cards live with a person or business.";
            _whoBox = WhoCombo();
            FieldsPanel.Children.Add(_whoBox);
        }
        else if (_step == 1)
        {
            QuestionText.Text = "Card nickname";
            HintText.Text = "e.g. Everyday rewards, Store card.";
            _nameBox = new TextBox { Header = "Nickname", PlaceholderText = "Rewards card" };
            _instBox = new TextBox { Header = "Issuer (optional)" };
            FieldsPanel.Children.Add(_nameBox);
            FieldsPanel.Children.Add(_instBox);
        }
        else if (_step == 2)
        {
            QuestionText.Text = "Balance owed and credit limit";
            HintText.Text = "Balance = what you owe now.";
            _balBox = new NumberBox { Header = "Balance owed ($)", Value = 0, Minimum = 0 };
            _limitBox = new NumberBox { Header = "Credit limit ($)", Minimum = 0 };
            FieldsPanel.Children.Add(_balBox);
            FieldsPanel.Children.Add(_limitBox);
        }
        else if (_step == 3)
        {
            QuestionText.Text = "When is payment due each month?";
            HintText.Text = "We need this to prove you can charge without interest.";
            _dueBox = new NumberBox { Header = "Due day (1–31)", Value = 15, Minimum = 1, Maximum = 31 };
            FieldsPanel.Children.Add(_dueBox);
        }
        else if (_step == 4)
        {
            QuestionText.Text = "Any 0% promo?";
            HintText.Text = "Optional. Helps us plan a set-aside so you never hit APR.";
            _promoBox = new CheckBox { Content = "Yes — I have a 0% promo" };
            _promoEndBox = new CalendarDatePicker { Header = "Promo end date" };
            _balBox = new NumberBox { Header = "Promo balance ($)", Minimum = 0 };
            FieldsPanel.Children.Add(_promoBox);
            FieldsPanel.Children.Add(_promoEndBox);
            FieldsPanel.Children.Add(_balBox);
        }
        else
        {
            QuestionText.Text = "Autopay & rewards";
            HintText.Text = "Optional autopay reminder and category cash-back rates for card picking.";
            _autopayBox = new ComboBox { Header = "Autopay" };
            _autopayBox.Items.Add(new ComboBoxItem { Content = "None for now", Tag = "none" });
            _autopayBox.Items.Add(new ComboBoxItem { Content = "Minimum only", Tag = "min" });
            _autopayBox.Items.Add(new ComboBoxItem { Content = "Pay statement in full", Tag = "statement" });
            _autopayBox.Items.Add(new ComboBoxItem { Content = "0% promo set-aside", Tag = "promo_sink" });
            _autopayBox.SelectedIndex = 0;
            FieldsPanel.Children.Add(_autopayBox);
            _rewardsUi = RewardsRatesUi.Build(compact: true);
            if (_rewardsRates is { Count: > 0 })
                _rewardsUi.ApplyRates(_rewardsRates);
            FieldsPanel.Children.Add(_rewardsUi.Root);
        }
    }

    private void RenderLoanSteps()
    {
        if (_step == 0)
        {
            QuestionText.Text = "Whose loan?";
            _whoBox = WhoCombo();
            FieldsPanel.Children.Add(_whoBox);
        }
        else if (_step == 1)
        {
            QuestionText.Text = "Loan nickname";
            _nameBox = new TextBox { Header = "Nickname", PlaceholderText = "Mortgage / car / student" };
            FieldsPanel.Children.Add(_nameBox);
        }
        else if (_step == 2)
        {
            QuestionText.Text = "Balance, APR, and minimum";
            HintText.Text = "APR as decimal — 0.065 means 6.5%.";
            _balBox = new NumberBox { Header = "Balance owed ($)", Minimum = 0 };
            _aprBox = new NumberBox { Header = "APR (e.g. 0.065)", Minimum = 0, Maximum = 1, SmallChange = 0.001 };
            _minBox = new NumberBox { Header = "Minimum payment ($)", Minimum = 0 };
            FieldsPanel.Children.Add(_balBox);
            FieldsPanel.Children.Add(_aprBox);
            FieldsPanel.Children.Add(_minBox);
        }
        else if (_step == 3)
        {
            QuestionText.Text = "Payment due day";
            _dueBox = new NumberBox { Header = "Due day (1–31)", Value = 1, Minimum = 1, Maximum = 31 };
            FieldsPanel.Children.Add(_dueBox);
        }
        else
        {
            QuestionText.Text = "Add this loan?";
            HintText.Text = "We'll use it for payoff order and opportunity-cost advice.";
        }
    }

    private void RenderBillSteps()
    {
        var isOut = _kind is "bill" or "owner_draw";
        if (_step == 0)
        {
            QuestionText.Text = _kind switch
            {
                "bill" => "What bill is this?",
                "owner_draw" => "Owner draw name",
                _ => "What income is this?",
            };
            _nameBox = new TextBox
            {
                Header = "Name",
                PlaceholderText = _kind switch
                {
                    "bill" => "Rent / utilities",
                    "owner_draw" => "Owner draw",
                    _ => "Paycheck",
                },
            };
            FieldsPanel.Children.Add(_nameBox);
            if (_kind is "bill" or "owner_draw")
            {
                _vendorBox = new TextBox { Header = "Vendor", PlaceholderText = "Who you pay (optional)" };
                FieldsPanel.Children.Add(_vendorBox);
            }
            else
            {
                _incomeSourceBox = new TextBox
                {
                    Header = "Income source",
                    PlaceholderText = "Job, retainers, rent…",
                };
                FieldsPanel.Children.Add(_incomeSourceBox);
            }
        }
        else if (_step == 1)
        {
            QuestionText.Text = "How much?";
            _amtBox = new NumberBox { Header = "Amount ($)", Minimum = 0.01, Value = 100 };
            FieldsPanel.Children.Add(_amtBox);
        }
        else if (_step == 2)
        {
            QuestionText.Text = "How often?";
            _cadenceBox = new ComboBox { Header = "Cadence" };
            foreach (var c in new[] { "monthly", "weekly", "biweekly", "semimonthly", "yearly" })
                _cadenceBox.Items.Add(new ComboBoxItem { Content = c, Tag = c });
            _cadenceBox.SelectedIndex = 0;
            FieldsPanel.Children.Add(_cadenceBox);
        }
        else if (_step == 3)
        {
            QuestionText.Text = "When?";
            HintText.Text = "Starts / Ends window the series; next date is the first hit.";
            _startDateBox = new CalendarDatePicker { Header = "Starts", Date = DateTimeOffset.Now };
            _nextDateBox = new CalendarDatePicker { Header = "Next date", Date = DateTimeOffset.Now.AddDays(7) };
            _endDateBox = new CalendarDatePicker { Header = "Ends (optional)" };
            FieldsPanel.Children.Add(_startDateBox);
            FieldsPanel.Children.Add(_nextDateBox);
            FieldsPanel.Children.Add(_endDateBox);
        }
        else if (_step == 4)
        {
            QuestionText.Text = _kind == "income"
                ? "Deposits to which account?"
                : "Pay from which account?";
            HintText.Text = "Cash or credit — this powers Safe to spend.";
            _accountBox = PayFromAccountCombo();
            if (_accountBox.Items.Count == 0)
            {
                HintText.Text = "No cash/credit accounts yet. Add checking or a card first.";
            }
            FieldsPanel.Children.Add(_accountBox);
        }
        else
        {
            QuestionText.Text = "Is the amount fixed?";
            _certaintyBox = new CheckBox
            {
                Content = isOut
                    ? (_kind == "owner_draw" ? "Yes — fixed draw" : "Yes — fixed bill")
                    : "Mostly reliable income",
                IsChecked = isOut,
            };
            HintText.Text = isOut
                ? "Uncheck if it varies (we'll treat it more carefully)."
                : "Uncheck if commission/variable — we'll haircut it in careful mode.";
            FieldsPanel.Children.Add(_certaintyBox);

            // Opex class when any selected/related profile is a business
            if (isOut && HasBusinessProfile())
            {
                _opexBox = new ComboBox { Header = "Opex class (business)" };
                _opexBox.Items.Add(new ComboBoxItem { Content = "Fixed", Tag = "fixed" });
                _opexBox.Items.Add(new ComboBoxItem { Content = "Variable", Tag = "variable" });
                _opexBox.SelectedIndex = 0;
                FieldsPanel.Children.Add(_opexBox);
            }
        }
    }

    private bool HasBusinessProfile()
        => _profiles.Any(p =>
            string.Equals(JsonUi.Str(p, "entity_type"), "business", StringComparison.OrdinalIgnoreCase));

    private void RenderEntitySteps()
    {
        if (_step == 0)
        {
            QuestionText.Text = _kind == "business" ? "Business name" : "Child's display name";
            HintText.Text = _kind == "business"
                ? "This gets its own Safe to spend silo."
                : "Allowance tracking — not a tax return.";
            _nameBox = new TextBox { Header = "Name" };
            FieldsPanel.Children.Add(_nameBox);
        }
        else if (_step == 1 && _kind == "business")
        {
            QuestionText.Text = "Rough tax shape";
            HintText.Text = "For organizing books — not filing.";
            _taxBox = new ComboBox { Header = "Type" };
            _taxBox.Items.Add(new ComboBoxItem { Content = "S-corp (1120-S)", Tag = "1120S" });
            _taxBox.Items.Add(new ComboBoxItem { Content = "Partnership / LLC (1065)", Tag = "1065" });
            _taxBox.Items.Add(new ComboBoxItem { Content = "Sole prop (Schedule C)", Tag = "SchC" });
            _taxBox.Items.Add(new ComboBoxItem { Content = "Other", Tag = "other" });
            _taxBox.SelectedIndex = 0;
            FieldsPanel.Children.Add(_taxBox);
        }
        else
        {
            QuestionText.Text = "Create this?";
            HintText.Text = "You can add checking for them next.";
        }
    }

    private ComboBox WhoCombo()
    {
        var box = new ComboBox { Header = UiCopy.Who, HorizontalAlignment = HorizontalAlignment.Stretch };
        var idx = 0;
        var i = 0;
        foreach (var p in _profiles)
        {
            var id = p.GetProperty("id").GetInt32();
            box.Items.Add(new ComboBoxItem
            {
                Content = JsonUi.Str(p, "display_name"),
                Tag = id,
            });
            if (AppState.SelectedProfileId == id) idx = i;
            i++;
        }
        if (box.Items.Count > 0) box.SelectedIndex = idx;
        return box;
    }

    private ComboBox AccountCombo()
    {
        var box = new ComboBox { Header = "Account", HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var a in _accounts)
        {
            box.Items.Add(new ComboBoxItem
            {
                Content = $"{JsonUi.Str(a, "nickname")} [{JsonUi.Str(a, "kind")}]",
                Tag = a.GetProperty("id").GetInt32(),
            });
        }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
        return box;
    }

    /// <summary>Pay from: cash (checking/savings) or credit only.</summary>
    private ComboBox PayFromAccountCombo()
    {
        var box = new ComboBox
        {
            Header = _kind == "income" ? "Deposits to" : "Pay from",
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        foreach (var a in _accounts)
        {
            var kind = JsonUi.Str(a, "kind", "").ToLowerInvariant();
            var isCash = a.TryGetProperty("is_cash_for_ifpp", out var ic) && ic.ValueKind == JsonValueKind.True;
            if (kind is not ("checking" or "savings" or "cash" or "credit") && !isCash)
                continue;
            var role = kind == "credit" ? "credit" : "cash";
            box.Items.Add(new ComboBoxItem
            {
                Content = $"{JsonUi.Str(a, "nickname")} [{role}]",
                Tag = a.GetProperty("id").GetInt32(),
            });
        }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
        return box;
    }

    private void Back_Click(object sender, RoutedEventArgs e)
    {
        if (_step > 0)
        {
            _step--;
            RenderStep();
        }
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        if (Frame?.CanGoBack == true) Frame.GoBack();
        else Frame?.Navigate(typeof(AddHubPage));
    }

    private async void Next_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (_step < MaxStep)
            {
                ValidateStep();
                CaptureStep();
                _step++;
                RenderStep();
                return;
            }
            await FinishAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    // captured values
    private int _whoId;
    private string _name = "";
    private string? _inst;
    private decimal _bal;
    private decimal _limit;
    private decimal _apr;
    private decimal _minPay;
    private int _due = 15;
    private decimal? _apy;
    private decimal _amt;
    private string _cadence = "monthly";
    private int _acctId;
    private string _tax = "1120S";
    private string _certainty = "fixed";
    private string _autopay = "none";
    private bool _hasPromo;
    private DateTimeOffset? _promoEnd;
    private decimal _promoBal;
    private DateTimeOffset? _nextDate;
    private DateTimeOffset? _startDate;
    private DateTimeOffset? _endDate;
    private string? _vendor;
    private string? _incomeSource;
    private string? _opexClass;

    private void ValidateStep()
    {
        if (_step == 0 && _kind is "cash" or "savings" or "card" or "loan")
        {
            if (_whoBox?.SelectedItem is not ComboBoxItem { Tag: int })
                throw new InvalidOperationException("Pick who this belongs to.");
        }
        if ((_kind is "cash" or "savings" or "card" or "loan" or "bill" or "income" or "owner_draw" or "business" or "child")
            && _step == 1 && !IsScheduleKind)
        {
            if (_kind is "cash" or "savings" or "card" or "loan" or "business" or "child")
            {
                if (_nameBox is not null && string.IsNullOrWhiteSpace(_nameBox.Text) && _step == 1)
                    throw new InvalidOperationException("Give it a name.");
            }
        }
        if (_kind == "card" && _step == 3)
        {
            if (_dueBox is null || double.IsNaN(_dueBox.Value))
                throw new InvalidOperationException("We need a payment due day to keep interest off the table.");
        }
        if (IsScheduleKind)
        {
            if (_step == 0 && string.IsNullOrWhiteSpace(_nameBox?.Text))
                throw new InvalidOperationException("Name it so you'll recognize it.");
            if (_step == 4 && (_accountBox?.SelectedItem is not ComboBoxItem { Tag: int }))
                throw new InvalidOperationException("Pick a Pay from account (cash or credit).");
        }
    }

    private void CaptureStep()
    {
        if (_whoBox?.SelectedItem is ComboBoxItem { Tag: int wid }) _whoId = wid;
        if (_nameBox is not null) _name = _nameBox.Text?.Trim() ?? "";
        if (_instBox is not null) _inst = string.IsNullOrWhiteSpace(_instBox.Text) ? null : _instBox.Text.Trim();
        if (_balBox is not null && !double.IsNaN(_balBox.Value)) _bal = (decimal)_balBox.Value;
        if (_limitBox is not null && !double.IsNaN(_limitBox.Value)) _limit = (decimal)_limitBox.Value;
        if (_aprBox is not null && !double.IsNaN(_aprBox.Value)) _apr = (decimal)_aprBox.Value;
        if (_minBox is not null && !double.IsNaN(_minBox.Value)) _minPay = (decimal)_minBox.Value;
        if (_dueBox is not null && !double.IsNaN(_dueBox.Value)) _due = (int)_dueBox.Value;
        if (_apyBox is not null && !double.IsNaN(_apyBox.Value)) _apy = (decimal)_apyBox.Value;
        if (_amtBox is not null && !double.IsNaN(_amtBox.Value)) _amt = (decimal)_amtBox.Value;
        if (_cadenceBox?.SelectedItem is ComboBoxItem { Tag: string cad }) _cadence = cad;
        if (_accountBox?.SelectedItem is ComboBoxItem { Tag: int aid }) _acctId = aid;
        if (_taxBox?.SelectedItem is ComboBoxItem { Tag: string tax }) _tax = tax;
        if (_certaintyBox is not null)
            _certainty = _certaintyBox.IsChecked == true ? "fixed" : "expected";
        if (_autopayBox?.SelectedItem is ComboBoxItem { Tag: string ap }) _autopay = ap;
        if (_promoBox is not null) _hasPromo = _promoBox.IsChecked == true;
        if (_promoEndBox is not null) _promoEnd = _promoEndBox.Date;
        if (_nextDateBox is not null) _nextDate = _nextDateBox.Date;
        if (_startDateBox is not null) _startDate = _startDateBox.Date;
        if (_endDateBox is not null) _endDate = _endDateBox.Date;
        if (_vendorBox is not null)
            _vendor = string.IsNullOrWhiteSpace(_vendorBox.Text) ? null : _vendorBox.Text.Trim();
        if (_incomeSourceBox is not null)
            _incomeSource = string.IsNullOrWhiteSpace(_incomeSourceBox.Text) ? null : _incomeSourceBox.Text.Trim();
        if (_opexBox?.SelectedItem is ComboBoxItem { Tag: string opex }) _opexClass = opex;
        // promo balance reuses _balBox on promo step — capture to _promoBal
        if (_kind == "card" && _step == 4 && _balBox is not null && !double.IsNaN(_balBox.Value))
            _promoBal = (decimal)_balBox.Value;
        if (_kind == "card" && _rewardsUi is not null)
            _rewardsRates = _rewardsUi.CollectRates();
    }

    private async Task FinishAsync()
    {
        CaptureStep();
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();

        if (_kind is "cash" or "savings")
        {
            if (_whoId == 0 && _whoBox?.SelectedItem is ComboBoxItem { Tag: int w }) _whoId = w;
            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = _whoId,
                ["kind"] = _kind == "savings" ? "savings" : "checking",
                ["nickname"] = string.IsNullOrWhiteSpace(_name) ? "Account" : _name,
                ["institution"] = _inst,
                ["current_balance"] = _bal,
                ["is_cash_for_ifpp"] = true,
            };
            if (_apy is not null) body["apy"] = _apy;
            await api.CreateAccountAsync(body);
            MsgText.Text = "Added. Safe to spend will update on Home.";
        }
        else if (_kind == "card")
        {
            if (_whoId == 0 && _whoBox?.SelectedItem is ComboBoxItem { Tag: int w }) _whoId = w;
            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = _whoId,
                ["kind"] = "credit",
                ["nickname"] = string.IsNullOrWhiteSpace(_name) ? "Card" : _name,
                ["institution"] = _inst,
                ["current_balance"] = _bal,
                ["credit_limit"] = _limit > 0 ? _limit : null,
                ["payment_due_day"] = _due,
                ["statement_close_day"] = 1,
                ["is_cash_for_ifpp"] = false,
            };
            if (_hasPromo)
            {
                body["promo_apr"] = 0m;
                if (_promoEnd is not null)
                    body["promo_end_date"] = _promoEnd.Value.Date.ToString("yyyy-MM-dd");
                body["promo_balance"] = _promoBal > 0 ? _promoBal : _bal;
            }
            var created = await api.CreateAccountAsync(body);
            var id = created.GetProperty("id").GetInt32();
            if (_autopay != "none")
                await api.SetAutopayAsync(id, _autopay);
            if (_rewardsRates is { Count: > 0 })
                await api.PutRewardsRatesAsync(id, _rewardsRates);
            else if (_rewardsUi is not null && _rewardsUi.HasRates())
                await api.PutRewardsRatesAsync(id, _rewardsUi.CollectRates());
            MsgText.Text = "Card added. We can prove interest-free charges when due day is set.";
        }
        else if (_kind == "loan")
        {
            if (_whoId == 0 && _whoBox?.SelectedItem is ComboBoxItem { Tag: int w }) _whoId = w;
            await api.CreateAccountAsync(new Dictionary<string, object?>
            {
                ["profile_id"] = _whoId,
                ["kind"] = "loan",
                ["nickname"] = string.IsNullOrWhiteSpace(_name) ? "Loan" : _name,
                ["current_balance"] = _bal,
                ["apr"] = _apr,
                ["min_payment"] = _minPay,
                ["payment_due_day"] = _due,
                ["is_cash_for_ifpp"] = false,
            });
            MsgText.Text = "Loan added — payoff planning will include it.";
        }
        else if (IsScheduleKind)
        {
            if (_acctId == 0 && _accountBox?.SelectedItem is ComboBoxItem { Tag: int a }) _acctId = a;
            var acct = _accounts.FirstOrDefault(x => x.GetProperty("id").GetInt32() == _acctId);
            var pid = acct.ValueKind != JsonValueKind.Undefined
                ? acct.GetProperty("profile_id").GetInt32()
                : (_profiles.FirstOrDefault().ValueKind != JsonValueKind.Undefined
                    ? _profiles[0].GetProperty("id").GetInt32()
                    : 1);
            var apiKind = _kind switch
            {
                "bill" => "expense",
                "owner_draw" => "owner_draw",
                _ => "income",
            };
            var signed = apiKind is "expense" or "owner_draw" ? -Math.Abs(_amt) : Math.Abs(_amt);
            var next = (_nextDate ?? DateTimeOffset.Now.AddDays(7)).Date.ToString("yyyy-MM-dd");
            var defaultName = _kind switch
            {
                "bill" => "Bill",
                "owner_draw" => "Owner draw",
                _ => "Income",
            };
            var name = string.IsNullOrWhiteSpace(_name) ? defaultName : _name;
            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = pid,
                ["name"] = name,
                ["amount"] = signed,
                ["next_date"] = next,
                ["cadence"] = _cadence,
                ["certainty"] = _certainty,
                ["kind"] = apiKind,
                ["account_id"] = _acctId,
                ["series_id"] = Guid.NewGuid().ToString("N"),
                ["series_label"] = name,
            };
            if (_startDate is not null)
                body["start_date"] = _startDate.Value.Date.ToString("yyyy-MM-dd");
            if (_endDate is not null)
                body["end_date"] = _endDate.Value.Date.ToString("yyyy-MM-dd");
            if (!string.IsNullOrWhiteSpace(_vendor))
                body["vendor"] = _vendor;
            if (apiKind == "income" && !string.IsNullOrWhiteSpace(_incomeSource))
                body["income_source"] = _incomeSource;
            if ((apiKind is "expense" or "owner_draw") && !string.IsNullOrWhiteSpace(_opexClass))
                body["opex_class"] = _opexClass;

            await api.CreateScheduledAsync(body);
            MsgText.Text = _kind switch
            {
                "bill" => "Bill scheduled — Safe to spend will protect checking.",
                "owner_draw" => "Owner draw scheduled.",
                _ => "Income scheduled.",
            };
        }
        else if (_kind is "business" or "child")
        {
            object body = _kind == "business"
                ? new { display_name = _name, entity_type = "business", tax_form_primary = _tax }
                : new
                {
                    display_name = _name,
                    entity_type = "child",
                    parent_profile_id = _profiles.FirstOrDefault(p => JsonUi.Str(p, "slug") == "personal") is { } pr
                        ? pr.GetProperty("id").GetInt32()
                        : (int?)null,
                };
            await api.CreateProfileAsync(body);
            MsgText.Text = "Created. Tip: Add checking for them under Add.";
        }

        await Task.Delay(600);
        Frame?.Navigate(typeof(HomePage));
    }
}
