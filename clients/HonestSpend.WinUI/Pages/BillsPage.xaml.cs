using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class BillsPage : Page
{
    private JsonElement _accounts;
    private JsonElement _profilesRaw;
    private readonly List<(int Id, string Name, string EntityType)> _profiles = new();
    private readonly Dictionary<int, string?> _seriesById = new();

    public BillsPage()
    {
        InitializeComponent();
        NextDateBox.Date = DateTimeOffset.Now;
        StartDateBox.Date = DateTimeOffset.Now;
        PayrollDateBox.Date = DateTimeOffset.Now;
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();
    private void WizardBill_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(MoneyWizardPage), "bill");
    private void WizardIncome_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(MoneyWizardPage), "income");
    private void WizardOwnerDraw_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(MoneyWizardPage), "owner_draw");

    private void Profile_Changed(object sender, SelectionChangedEventArgs e)
    {
        FillAccounts();
        FillPayrollCash();
        UpdateConditionalFields();
    }

    private void Kind_Changed(object sender, SelectionChangedEventArgs e)
    {
        UpdateConditionalFields();
        // Account header depends on kind
        if (AccountBox is not null)
        {
            var kind = SelectedKind();
            AccountBox.Header = kind == "income" ? "Deposits to" : "Pay from";
        }
    }

    private string SelectedKind()
    {
        if (KindBox.SelectedItem is ComboBoxItem ki && ki.Tag is string ks)
            return ks;
        return "expense";
    }

    private void UpdateConditionalFields()
    {
        if (IncomeSourceBox is null || OpexBox is null) return;
        var kind = SelectedKind();
        IncomeSourceBox.Visibility = kind == "income" ? Visibility.Visible : Visibility.Collapsed;

        var isBusiness = false;
        if (ProfileBox.SelectedItem is ComboBoxItem pi && pi.Tag is int profileId)
        {
            var match = _profiles.FirstOrDefault(p => p.Id == profileId);
            isBusiness = string.Equals(match.EntityType, "business", StringComparison.OrdinalIgnoreCase);
        }
        OpexBox.Visibility = isBusiness && kind is "expense" or "owner_draw"
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            _profilesRaw = await api.GetProfilesAsync();
            _profiles.Clear();
            ProfileBox.Items.Clear();
            foreach (var p in _profilesRaw.EnumerateArray())
            {
                var id = p.GetProperty("id").GetInt32();
                var name = JsonUi.Str(p, "display_name");
                var et = JsonUi.Str(p, "entity_type", "personal");
                _profiles.Add((id, name, et));
                ProfileBox.Items.Add(new ComboBoxItem { Content = name, Tag = id });
            }
            if (ProfileBox.Items.Count > 0) ProfileBox.SelectedIndex = 0;

            _accounts = await api.GetAccountsAsync();
            FillAccounts();
            FillPayrollCash();
            UpdateConditionalFields();

            var sched = await api.GetScheduledAsync();
            _seriesById.Clear();
            var rows = new List<BillRow>();
            foreach (var s in sched.EnumerateArray())
            {
                var id = s.GetProperty("id").GetInt32();
                var active = s.TryGetProperty("active", out var ac) && ac.GetBoolean();
                var seriesId = JsonUi.Str(s, "series_id", "");
                if (string.IsNullOrWhiteSpace(seriesId) || seriesId == "—")
                    seriesId = null;
                _seriesById[id] = seriesId;

                var kindLabel = KindLabel(JsonUi.Str(s, "kind"));
                var vendor = JsonUi.Str(s, "vendor", "");
                var title = $"{JsonUi.Str(s, "name")} · {kindLabel} · {JsonUi.Money(s, "amount")}";
                if (!string.IsNullOrWhiteSpace(vendor) && vendor != "—")
                    title += $" · {vendor}";

                var payFrom = JsonUi.Str(s, "account_nickname", "no account");
                var start = JsonUi.Str(s, "start_date", "");
                var end = JsonUi.Str(s, "end_date", "");
                var sub =
                    $"{JsonUi.Str(s, "profile_name")} · Pay from {payFrom} · " +
                    $"next {JsonUi.Str(s, "next_date")} · {JsonUi.Str(s, "cadence")} · {JsonUi.Str(s, "certainty")}";
                if (!string.IsNullOrWhiteSpace(start) && start != "—")
                    sub += $" · Starts {start}";
                if (!string.IsNullOrWhiteSpace(end) && end != "—")
                    sub += $" · Ends {end}";
                var opex = JsonUi.Str(s, "opex_class", "");
                if (!string.IsNullOrWhiteSpace(opex) && opex != "—")
                    sub += $" · Opex {opex}";
                var src = JsonUi.Str(s, "income_source", "");
                if (!string.IsNullOrWhiteSpace(src) && src != "—")
                    sub += $" · Source {src}";
                if (!active) sub += " · ENDED";

                var canStep = active && !string.IsNullOrWhiteSpace(seriesId);
                // Mark paid: active expense outflows only (amount < 0)
                var canMarkPaid = active && IsExpenseOutflow(s);
                rows.Add(new BillRow(
                    id,
                    title,
                    sub,
                    active ? Visibility.Visible : Visibility.Collapsed,
                    canStep ? Visibility.Visible : Visibility.Collapsed,
                    canMarkPaid ? Visibility.Visible : Visibility.Collapsed));
            }
            BillList.ItemsSource = rows;
            MsgText.Text = $"{rows.Count} recurring items";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static string KindLabel(string kind) => kind switch
    {
        "expense" => "Bill",
        "income" => "Income",
        "owner_draw" => "Owner draw",
        _ => kind,
    };

    private void FillAccounts()
    {
        AccountBox.Items.Clear();
        if (ProfileBox.SelectedItem is not ComboBoxItem pi || pi.Tag is not int profileId)
            return;
        if (_accounts.ValueKind != JsonValueKind.Array)
            return;

        // Pay from: cash (checking/savings/cash) or credit — loans omitted for bills
        foreach (var a in _accounts.EnumerateArray())
        {
            if (a.GetProperty("profile_id").GetInt32() != profileId) continue;
            var kind = JsonUi.Str(a, "kind", "").ToLowerInvariant();
            var isCash = a.TryGetProperty("is_cash_for_ifpp", out var ic) && ic.ValueKind == JsonValueKind.True;
            if (kind is not ("checking" or "savings" or "cash" or "credit") && !isCash)
                continue;
            var id = a.GetProperty("id").GetInt32();
            var role = kind == "credit" ? "credit" : "cash";
            var label = $"{JsonUi.Str(a, "nickname")} [{role}]";
            AccountBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
        }
        if (AccountBox.Items.Count > 0) AccountBox.SelectedIndex = 0;
    }

    private void FillPayrollCash()
    {
        if (PayrollCashBox is null) return;
        PayrollCashBox.Items.Clear();
        if (ProfileBox.SelectedItem is not ComboBoxItem pi || pi.Tag is not int profileId)
            return;
        if (_accounts.ValueKind != JsonValueKind.Array)
            return;
        foreach (var a in _accounts.EnumerateArray())
        {
            if (a.GetProperty("profile_id").GetInt32() != profileId) continue;
            var kind = JsonUi.Str(a, "kind", "").ToLowerInvariant();
            var isCash = a.TryGetProperty("is_cash_for_ifpp", out var ic) && ic.ValueKind == JsonValueKind.True;
            if (kind is not ("checking" or "savings" or "cash") && !isCash)
                continue;
            var id = a.GetProperty("id").GetInt32();
            PayrollCashBox.Items.Add(new ComboBoxItem
            {
                Content = JsonUi.Str(a, "nickname"),
                Tag = id,
            });
        }
        if (PayrollCashBox.Items.Count > 0) PayrollCashBox.SelectedIndex = 0;
    }

    private async void PayrollPackage_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        PayrollMsgText.Text = "";
        try
        {
            if (ProfileBox.SelectedItem is not ComboBoxItem pitem || pitem.Tag is not int profileId)
                throw new InvalidOperationException("Pick entity (Who).");
            if (PayrollCashBox.SelectedItem is not ComboBoxItem cashItem || cashItem.Tag is not int cashId)
                throw new InvalidOperationException("Pick a cash account to pay from.");
            var net = double.IsNaN(PayrollNetBox.Value) ? 0m : (decimal)PayrollNetBox.Value;
            var tax = double.IsNaN(PayrollTaxBox.Value) ? 0m : (decimal)PayrollTaxBox.Value;
            if (net <= 0 && tax <= 0)
                throw new InvalidOperationException("Enter net payroll and/or employer tax.");
            var cadence = "biweekly";
            if (PayrollCadenceBox.SelectedItem is ComboBoxItem ci && ci.Tag is string cs)
                cadence = cs;
            var payDate = PayrollDateBox.Date?.Date ?? DateTime.Today;
            var name = string.IsNullOrWhiteSpace(PayrollNameBox.Text)
                ? "Biweekly payroll"
                : PayrollNameBox.Text.Trim();

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CreatePayrollPackageAsync(new Dictionary<string, object?>
            {
                ["profile_id"] = profileId,
                ["name"] = name,
                ["pay_date"] = payDate.ToString("yyyy-MM-dd"),
                ["cadence"] = cadence,
                ["net_payroll"] = net,
                ["employer_tax"] = tax,
                ["cash_account_id"] = cashId,
                ["series_label"] = name,
            });
            var pkg = JsonUi.Str(res, "package_id", "");
            PayrollMsgText.Text =
                $"Payroll day created · net #{JsonUi.Int(res, "net_id")} · tax #{JsonUi.Int(res, "tax_id")}" +
                (string.IsNullOrEmpty(pkg) || pkg == "—" ? "" : $" · package {pkg[..Math.Min(8, pkg.Length)]}…");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (ProfileBox.SelectedItem is not ComboBoxItem pitem || pitem.Tag is not int profileId)
                throw new InvalidOperationException("Pick entity.");
            var kind = SelectedKind();
            int? accountId = null;
            if (AccountBox.SelectedItem is ComboBoxItem ai && ai.Tag is int aid)
                accountId = aid;
            if ((kind is "expense" or "owner_draw") && accountId is null)
                throw new InvalidOperationException(kind == "owner_draw"
                    ? "Owner draw requires a Pay from account."
                    : "Bills require a Pay from account (cash or credit).");

            var cadence = "monthly";
            if (CadenceBox.SelectedItem is ComboBoxItem ci && ci.Tag is string cs) cadence = cs;
            var certainty = "fixed";
            if (CertaintyBox.SelectedItem is ComboBoxItem cei && cei.Tag is string ces) certainty = ces;

            var next = NextDateBox.Date?.Date ?? DateTime.Today;
            var start = StartDateBox.Date?.Date;
            var end = EndDateBox.Date?.Date;

            var name = NameBox.Text?.Trim() ?? "Recurring";
            var seriesId = Guid.NewGuid().ToString("N");

            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = profileId,
                ["name"] = name,
                ["amount"] = double.IsNaN(AmtBox.Value) ? 0 : (decimal)AmtBox.Value,
                ["next_date"] = next.ToString("yyyy-MM-dd"),
                ["cadence"] = cadence,
                ["certainty"] = certainty,
                ["kind"] = kind,
                ["account_id"] = accountId,
                ["active"] = true,
                ["series_id"] = seriesId,
                ["series_label"] = name,
            };

            if (start is not null)
                body["start_date"] = start.Value.ToString("yyyy-MM-dd");
            if (end is not null)
                body["end_date"] = end.Value.ToString("yyyy-MM-dd");

            var vendor = VendorBox.Text?.Trim();
            if (!string.IsNullOrWhiteSpace(vendor))
                body["vendor"] = vendor;

            if (kind == "income")
            {
                var src = IncomeSourceBox.Text?.Trim();
                if (!string.IsNullOrWhiteSpace(src))
                    body["income_source"] = src;
            }

            if (OpexBox.Visibility == Visibility.Visible
                && OpexBox.SelectedItem is ComboBoxItem oi && oi.Tag is string opex)
            {
                body["opex_class"] = opex;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateScheduledAsync(body);
            MsgText.Text = kind switch
            {
                "owner_draw" => "Owner draw scheduled.",
                "income" => "Income scheduled.",
                _ => "Bill scheduled.",
            };
            NameBox.Text = "";
            VendorBox.Text = "";
            IncomeSourceBox.Text = "";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void ChangeAmount_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        if (!_seriesById.TryGetValue(id, out var seriesId) || string.IsNullOrWhiteSpace(seriesId))
        {
            ErrorBar.Message = "This item has no series id — re-save as a series to change amount on a date.";
            ErrorBar.IsOpen = true;
            return;
        }

        var amtBox = new NumberBox
        {
            Header = "New amount ($ positive)",
            Minimum = 0.01,
            Value = 100,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
        };
        var effectiveBox = new CalendarDatePicker
        {
            Header = "Effective from",
            Date = DateTimeOffset.Now,
        };
        var panel = new StackPanel { Spacing = 12, MinWidth = 320 };
        panel.Children.Add(new TextBlock
        {
            Text = "Change amount on date… Closes the current series segment and starts a new amount from this date.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });
        panel.Children.Add(amtBox);
        panel.Children.Add(effectiveBox);

        var dlg = new ContentDialog
        {
            Title = "Change amount on date",
            Content = panel,
            PrimaryButtonText = "Apply step",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = XamlRoot,
        };

        if (await dlg.ShowAsync() != ContentDialogResult.Primary)
            return;

        try
        {
            if (double.IsNaN(amtBox.Value) || amtBox.Value <= 0)
                throw new InvalidOperationException("Enter a new amount.");
            var effective = effectiveBox.Date?.Date ?? DateTime.Today;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.AddSeriesStepAsync(new Dictionary<string, object?>
            {
                ["series_id"] = seriesId,
                ["new_amount"] = (decimal)amtBox.Value,
                ["effective_from"] = effective.ToString("yyyy-MM-dd"),
            });
            MsgText.Text = $"Amount step applied from {effective:yyyy-MM-dd}.";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void End_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.EndScheduledAsync(id, "Ended from WinUI");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void MarkPaid_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        ErrorBar.IsOpen = false;
        SuccessBar.IsOpen = false;
        try
        {
            btn.IsEnabled = false;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            JsonElement res;
            try
            {
                res = await api.MarkSchedulePaidAsync(id, createTransaction: true);
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

                res = await api.MarkSchedulePaidAsync(id, createTransaction: true, confirmUnsafe: true);
            }

            var name = JsonUi.Str(res, "name", "Bill");
            var next = JsonUi.Str(res, "next_date", "");
            var matched = res.TryGetProperty("matched", out var mt) && mt.ValueKind == JsonValueKind.True;
            var ended = res.TryGetProperty("ended", out var en) && en.ValueKind == JsonValueKind.True;
            string msg;
            if (matched)
                msg = string.IsNullOrEmpty(next) || next == "—"
                    ? $"{name}: matched bank import — no second post"
                    : $"{name}: matched bank import · next {next}";
            else if (ended)
                msg = $"{name} paid through end";
            else
                msg = string.IsNullOrEmpty(next) || next == "—"
                    ? $"{name} marked paid"
                    : $"{name} marked paid · next {next}";

            SuccessBar.Title = matched ? "Matched bank payment" : "Marked paid";
            SuccessBar.Message = msg;
            SuccessBar.IsOpen = true;
            MsgText.Text = msg;
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
        finally
        {
            btn.IsEnabled = true;
        }
    }

    private static bool IsExpenseOutflow(JsonElement s)
    {
        var kind = JsonUi.Str(s, "kind", "").ToLowerInvariant();
        if (kind is "income")
            return false;
        // Prefer signed amount: expenses/owner_draw stored negative
        var raw = JsonUi.Str(s, "amount", "0");
        if (decimal.TryParse(raw, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return d < 0;
        // Fallback: treat expense / owner_draw kinds as outflows
        return kind is "expense" or "owner_draw" or "bill" or "";
    }

    private sealed record BillRow(
        int Id,
        string Title,
        string Subtitle,
        Visibility EndVisible,
        Visibility StepVisible,
        Visibility MarkPaidVisible);
}
