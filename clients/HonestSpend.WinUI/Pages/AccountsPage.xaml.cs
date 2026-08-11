using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class AccountsPage : Page
{
    private readonly List<(int Id, string Name)> _profiles = new();
    private JsonElement _accountsRaw = default;

    public AccountsPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();
    private void Wizard_Click(object sender, RoutedEventArgs e) => Frame?.Navigate(typeof(AddHubPage));

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            var profiles = await api.GetProfilesAsync();
            _profiles.Clear();
            ProfileBox.Items.Clear();
            foreach (var p in profiles.EnumerateArray())
            {
                var id = p.GetProperty("id").GetInt32();
                var name = JsonUi.Str(p, "display_name");
                _profiles.Add((id, name));
                ProfileBox.Items.Add(new ComboBoxItem { Content = name, Tag = id });
            }
            if (ProfileBox.Items.Count > 0) ProfileBox.SelectedIndex = 0;

            _accountsRaw = await api.GetAccountsAsync();
            var rows = new List<AccountRow>();
            foreach (var a in _accountsRaw.EnumerateArray())
            {
                var id = a.GetProperty("id").GetInt32();
                var kind = JsonUi.Str(a, "kind");
                var pid = a.GetProperty("profile_id").GetInt32();
                var pname = _profiles.FirstOrDefault(x => x.Id == pid).Name ?? "Unknown";
                var kindLabel = kind switch
                {
                    "checking" => "Checking",
                    "savings" => "Savings",
                    "credit" => "Credit",
                    "cash" => "Cash",
                    "loan" => "Loan",
                    _ => kind,
                };
                var title = $"{JsonUi.Str(a, "nickname")} · {kindLabel}";
                var bal = JsonUi.Money(a, "current_balance");
                var sub = $"{pname} · {bal}";
                if (kind == "credit")
                {
                    sub +=
                        $" · close {JsonUi.Str(a, "statement_close_day", "?")} · " +
                        $"due day {JsonUi.Str(a, "payment_due_day", "?")} · limit {JsonUi.Money(a, "credit_limit")}";
                    if (a.TryGetProperty("promo_end_date", out var pe) && pe.ValueKind != JsonValueKind.Null)
                        sub += $" · promo ends {pe.GetString()}";
                }
                var apy = JsonUi.Str(a, "apy", "");
                var meta = JsonUi.Str(a, "institution", "");
                if (!string.IsNullOrEmpty(apy) && apy != "—")
                    meta += (meta.Length > 0 ? " · " : "") + $"APY {apy}";
                if (a.TryGetProperty("is_cash_for_ifpp", out var ifpp) && ifpp.ValueKind == JsonValueKind.True)
                    meta += " · Safe to spend";
                if (kind == "credit")
                {
                    var nextPay = JsonUi.Str(a, "next_payment_amount_cached", "");
                    var nextDue = JsonUi.Str(a, "next_payment_date_cached", "");
                    if (!string.IsNullOrEmpty(nextPay) && nextPay != "—" && nextPay != "0" && nextPay != "0.00")
                    {
                        meta += (meta.Length > 0 ? " · " : "") +
                                $"next pay {JsonUi.Money(a, "next_payment_amount_cached")}";
                        if (!string.IsNullOrEmpty(nextDue) && nextDue != "—")
                            meta += $" on {nextDue}";
                    }
                    var stmt = JsonUi.Str(a, "statement_balance_cached", "");
                    if (!string.IsNullOrEmpty(stmt) && stmt != "—" && stmt != "0" && stmt != "0.00")
                        meta += (meta.Length > 0 ? " · " : "") +
                                $"statement {JsonUi.Money(a, "statement_balance_cached")}";
                }
                rows.Add(new AccountRow(id, title, sub, meta, kind));
            }
            AccountList.ItemsSource = rows;
            MsgText.Text = $"{rows.Count} accounts";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Archive_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not AccountRow row) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.ArchiveAccountAsync(row.Id);
            MsgText.Text = $"Archived {row.Title} (hidden from Safe to spend).";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Edit_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not AccountRow row) return;
        ErrorBar.IsOpen = false;

        JsonElement? raw = null;
        if (_accountsRaw.ValueKind == JsonValueKind.Array)
        {
            foreach (var a in _accountsRaw.EnumerateArray())
            {
                if (a.GetProperty("id").GetInt32() == row.Id)
                {
                    raw = a;
                    break;
                }
            }
        }
        if (raw is null)
        {
            ErrorBar.Message = "Account not found.";
            ErrorBar.IsOpen = true;
            return;
        }

        var a0 = raw.Value;
        var nickBox = new TextBox
        {
            Header = "Nickname",
            Text = JsonUi.Str(a0, "nickname"),
        };
        var instBox = new TextBox
        {
            Header = "Institution",
            Text = JsonUi.Str(a0, "institution", ""),
        };
        var balBox = new NumberBox
        {
            Header = row.Kind == "credit" ? "Balance owed" : "Balance",
            Value = ParseDouble(JsonUi.Str(a0, "current_balance", "0")),
        };
        var apyBox = new NumberBox
        {
            Header = "APY (e.g. 0.06)",
            Value = ParseDouble(JsonUi.Str(a0, "apy", "NaN")),
        };
        var limitBox = new NumberBox
        {
            Header = "Credit limit",
            Value = ParseDouble(JsonUi.Str(a0, "credit_limit", "NaN")),
        };
        var dueBox = new NumberBox
        {
            Header = "Payment due day (1–31)",
            Minimum = 1,
            Maximum = 31,
            Value = ParseDouble(JsonUi.Str(a0, "payment_due_day", "NaN")),
        };
        var closeBox = new NumberBox
        {
            Header = "Statement close day (1–31)",
            Minimum = 1,
            Maximum = 31,
            Value = ParseDouble(JsonUi.Str(a0, "statement_close_day", "NaN")),
        };
        var promoAprBox = new NumberBox
        {
            Header = "Promo APR (0 for 0%)",
            Value = ParseDouble(JsonUi.Str(a0, "promo_apr", "NaN")),
        };
        var promoEndBox = new CalendarDatePicker { Header = "Promo end" };
        var peStr = JsonUi.Str(a0, "promo_end_date", "");
        if (DateTime.TryParse(peStr, out var peDate))
            promoEndBox.Date = new DateTimeOffset(peDate.Date);
        var ifppBox = new CheckBox
        {
            Content = UiCopy.SpendableCash,
            IsChecked = a0.TryGetProperty("is_cash_for_ifpp", out var ifpp) && ifpp.ValueKind == JsonValueKind.True,
        };

        var panel = new StackPanel { Spacing = 10, MinWidth = 320 };
        panel.Children.Add(nickBox);
        panel.Children.Add(instBox);
        panel.Children.Add(balBox);
        if (row.Kind is "checking" or "savings" or "cash")
        {
            panel.Children.Add(apyBox);
            panel.Children.Add(ifppBox);
        }
        if (row.Kind == "credit")
        {
            panel.Children.Add(limitBox);
            panel.Children.Add(closeBox);
            panel.Children.Add(dueBox);
            panel.Children.Add(promoAprBox);
            panel.Children.Add(promoEndBox);
        }

        var dlg = new ContentDialog
        {
            Title = $"Edit · {row.Title}",
            Content = new ScrollViewer
            {
                Content = panel,
                MaxHeight = 480,
            },
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = XamlRoot,
        };

        if (await dlg.ShowAsync() != ContentDialogResult.Primary)
            return;

        try
        {
            var body = new Dictionary<string, object?>
            {
                ["nickname"] = nickBox.Text?.Trim() ?? "Account",
                ["institution"] = string.IsNullOrWhiteSpace(instBox.Text) ? null : instBox.Text.Trim(),
                ["current_balance"] = double.IsNaN(balBox.Value) ? 0m : (decimal)balBox.Value,
            };

            if (row.Kind is "checking" or "savings" or "cash")
            {
                body["is_cash_for_ifpp"] = ifppBox.IsChecked == true;
                if (!double.IsNaN(apyBox.Value) && apyBox.Value >= 0)
                    body["apy"] = (decimal)apyBox.Value;
            }
            if (row.Kind == "credit")
            {
                body["is_cash_for_ifpp"] = false;
                if (!double.IsNaN(limitBox.Value) && limitBox.Value > 0)
                {
                    body["credit_limit"] = (decimal)limitBox.Value;
                    var bal = double.IsNaN(balBox.Value) ? 0 : balBox.Value;
                    body["available_credit"] = (decimal)Math.Max(0, limitBox.Value - bal);
                }
                if (!double.IsNaN(closeBox.Value) && closeBox.Value is >= 1 and <= 31)
                    body["statement_close_day"] = (int)closeBox.Value;
                if (!double.IsNaN(dueBox.Value) && dueBox.Value is >= 1 and <= 31)
                    body["payment_due_day"] = (int)dueBox.Value;
                if (!double.IsNaN(promoAprBox.Value))
                    body["promo_apr"] = (decimal)promoAprBox.Value;
                if (promoEndBox.Date is not null)
                    body["promo_end_date"] = promoEndBox.Date.Value.Date.ToString("yyyy-MM-dd");
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.PatchAccountAsync(row.Id, body);

            // Close/due drive payment schedules — recompute via cycle-config PUT
            if (row.Kind == "credit")
            {
                var cycleBody = new Dictionary<string, object?>();
                if (!double.IsNaN(closeBox.Value) && closeBox.Value is >= 1 and <= 31)
                    cycleBody["statement_close_day"] = (int)closeBox.Value;
                if (!double.IsNaN(dueBox.Value) && dueBox.Value is >= 1 and <= 31)
                    cycleBody["payment_due_day"] = (int)dueBox.Value;
                if (cycleBody.Count > 0)
                    await api.PutAccountCycleConfigAsync(row.Id, cycleBody);
            }

            MsgText.Text = $"Updated {nickBox.Text?.Trim() ?? row.Title}.";
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
                throw new InvalidOperationException("Pick an entity profile.");

            var kind = "checking";
            if (KindBox.SelectedItem is ComboBoxItem k && k.Tag is string kt)
                kind = kt;

            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = profileId,
                ["kind"] = kind,
                ["nickname"] = NameBox.Text?.Trim() ?? "Account",
                ["institution"] = string.IsNullOrWhiteSpace(InstBox.Text) ? null : InstBox.Text.Trim(),
                ["current_balance"] = Num(BalBox),
                ["is_cash_for_ifpp"] = kind != "credit" && (IfppBox.IsChecked ?? false),
            };

            if (kind == "credit")
            {
                body["is_cash_for_ifpp"] = false;
                var lim = Num(LimitBox);
                if (lim > 0)
                {
                    body["credit_limit"] = lim;
                    body["available_credit"] = Math.Max(0, lim - Num(BalBox));
                }
                var due = Num(DueBox);
                if (due >= 1 && due <= 31) body["payment_due_day"] = (int)due;
                if (!double.IsNaN(PromoAprBox.Value))
                    body["promo_apr"] = (decimal)PromoAprBox.Value;
                if (PromoEndBox.Date is not null)
                    body["promo_end_date"] = PromoEndBox.Date.Value.Date.ToString("yyyy-MM-dd");
            }
            else if (!double.IsNaN(ApyBox.Value) && ApyBox.Value > 0)
            {
                body["apy"] = (decimal)ApyBox.Value;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateAccountAsync(body);
            MsgText.Text = "Account saved.";
            NameBox.Text = "";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static decimal Num(NumberBox box)
    {
        if (double.IsNaN(box.Value)) return 0;
        return (decimal)box.Value;
    }

    private static double ParseDouble(string s)
    {
        if (string.IsNullOrWhiteSpace(s) || s == "—" || s.Equals("NaN", StringComparison.OrdinalIgnoreCase))
            return double.NaN;
        return double.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d) ? d : double.NaN;
    }

    private sealed record AccountRow(int Id, string Title, string Subtitle, string Meta, string Kind);
}
