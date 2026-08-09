using System.Globalization;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

/// <summary>2-minute first-run: welcome → checking → card? → biggest bill? → done.</summary>
public sealed partial class FirstRunPage : Page
{
    private int _step;
    /// <summary>0 welcome · 1 cash · 2 card · 3 bill · 4 bank tip · 5 review · 6 done</summary>
    private const int MaxStep = 6;

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

    public FirstRunPage()
    {
        InitializeComponent();
    }

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        _step = 0;
        Render();
    }

    private void Render()
    {
        ErrorBar.IsOpen = false;
        Fields.Children.Clear();
        StepLabel.Text = $"Step {_step + 1} of {MaxStep + 1}";
        BackBtn.IsEnabled = _step > 0;
        NextBtn.Content = _step >= MaxStep ? "Finish" : "Next";

        switch (_step)
        {
            case 0:
                QuestionText.Text = "We'll answer one question";
                HintText.Text =
                    "What can you safely spend without bouncing checking or paying dumb interest? " +
                    "About two minutes. No spreadsheet.";
                break;
            case 1:
                QuestionText.Text = "Your primary checking";
                HintText.Text = "Rainy-day floor stays out of Safe to spend (default $1,000).";
                _cashName = new TextBox { Header = "Nickname", Text = _cashNameV };
                _inst = new TextBox { Header = "Bank (optional)", Text = _instV ?? "" };
                _cashBal = new NumberBox { Header = "Balance today ($)", Value = (double)_cashBalV, Minimum = 0 };
                _buffer = new NumberBox { Header = "Rainy-day floor ($)", Value = (double)_bufferV, Minimum = 0 };
                Fields.Children.Add(_cashName);
                Fields.Children.Add(_inst);
                Fields.Children.Add(_cashBal);
                Fields.Children.Add(_buffer);
                break;
            case 2:
                QuestionText.Text = "Add a credit card now?";
                HintText.Text = "Optional. Due day lets us prove interest-free charges.";
                _wantCard = new CheckBox { Content = "Yes — add a card", IsChecked = _wantCardV };
                _wantCard.Checked += (_, _) => CardFields(true);
                _wantCard.Unchecked += (_, _) => CardFields(false);
                Fields.Children.Add(_wantCard);
                CardFields(_wantCardV);
                break;
            case 3:
                QuestionText.Text = "Biggest monthly bill?";
                HintText.Text = "Optional. Rent/housing makes Safe to spend realistic on day one.";
                _wantBill = new CheckBox { Content = "Yes — add one bill", IsChecked = _wantBillV };
                _wantBill.Checked += (_, _) => BillFields(true);
                _wantBill.Unchecked += (_, _) => BillFields(false);
                Fields.Children.Add(_wantBill);
                BillFields(_wantBillV);
                break;
            case 4:
                QuestionText.Text = "Link your bank later?";
                HintText.Text =
                    "Manual balances work today. When you're ready, Full books → Banks (Plaid) or Import CSV. " +
                    "No bank login required to start.";
                Fields.Children.Add(new TextBlock
                {
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.85,
                    Text =
                        "Tip: start with the numbers you know. Link banks once Safe to spend feels right — " +
                        "that keeps first-run under two minutes.",
                });
                break;
            case 5:
                QuestionText.Text = "Review";
                HintText.Text = "We'll create these and show your first Safe to spend number.";
                var lines = new List<string>
                {
                    $"Checking: {_cashNameV} · {_cashBalV:C}",
                    $"Rainy-day floor: {_bufferV:C}",
                };
                if (_wantCardV)
                    lines.Add($"Card: {_cardNameV} · owed {_cardBalV:C} · due day {_cardDueV}");
                if (_wantBillV)
                    lines.Add($"Bill: {_billNameV} · {_billAmtV:C}/mo");
                lines.Add("Bank link: later (optional)");
                Fields.Children.Add(new ItemsControl { ItemsSource = lines });
                break;
            default:
                QuestionText.Text = "You're set";
                HintText.Text = "Open Home for Safe to spend, Do this next, and your 3-minute check.";
                NextBtn.Content = "Go to Home";
                break;
        }
    }

    private void CardFields(bool show)
    {
        // remove previous card fields beyond checkbox
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

    private void Capture()
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
    }

    private void Back_Click(object sender, RoutedEventArgs e)
    {
        if (_step > 0)
        {
            Capture();
            _step--;
            Render();
        }
    }

    private async void Next_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            Capture();
            if (_step == 1 && string.IsNullOrWhiteSpace(_cashNameV))
                throw new InvalidOperationException("Name your checking account.");
            if (_step == 2 && _wantCardV && (_cardDueV < 1 || _cardDueV > 31))
                throw new InvalidOperationException("Card needs a payment due day (1–31).");
            if (_step == 3 && _wantBillV && _billAmtV <= 0)
                throw new InvalidOperationException("Bill amount must be greater than zero.");

            // 0–4 advance; 5 = review → submit; 6 = go home
            if (_step < 5)
            {
                _step++;
                Render();
                return;
            }

            if (_step == 5)
            {
                await SubmitAsync();
                _step = 6;
                QuestionText.Text = "You're set";
                HintText.Text = string.IsNullOrWhiteSpace(MsgText.Text)
                    ? "Open Home for Safe to spend, Do this next, and your 3-minute check."
                    : MsgText.Text;
                Fields.Children.Clear();
                NextBtn.Content = "Go to Home";
                StepLabel.Text = "Done";
                BackBtn.IsEnabled = false;
                return;
            }

            Frame?.Navigate(typeof(HomePage));
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task SubmitAsync()
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
            body["bill_next_date"] = _billNextV.Date.ToString("yyyy-MM-dd");
        }

        var res = await api.FirstRunAsync(body);
        var home = await api.GetHomeSimpleAsync();
        var safe = JsonUi.Str(home, "safe_to_spend");
        if (decimal.TryParse(safe, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            safe = d.ToString("C", CultureInfo.CurrentCulture);
        MsgText.Text =
            $"Created. Safe to spend right now: {safe}. " +
            JsonUi.Str(res, "backup", "") is { Length: > 0 } bak
                ? $"Backup saved ({bak})."
                : "";
    }

    private async void Skip_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CompleteOnboardingAsync();
            Frame?.Navigate(typeof(HomePage));
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
