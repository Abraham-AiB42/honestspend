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
                rows.Add(new BillRow(
                    id,
                    title,
                    sub,
                    active ? Visibility.Visible : Visibility.Collapsed,
                    canStep ? Visibility.Visible : Visibility.Collapsed));
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

    private sealed record BillRow(
        int Id,
        string Title,
        string Subtitle,
        Visibility EndVisible,
        Visibility StepVisible);
}
