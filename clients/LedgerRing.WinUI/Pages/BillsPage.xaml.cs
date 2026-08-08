using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class BillsPage : Page
{
    private JsonElement _accounts;
    private readonly List<(int Id, string Name)> _profiles = new();

    public BillsPage()
    {
        InitializeComponent();
        NextDateBox.Date = DateTimeOffset.Now;
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();

    private async void Profile_Changed(object sender, SelectionChangedEventArgs e)
        => FillAccounts();

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

            _accounts = await api.GetAccountsAsync();
            FillAccounts();

            var sched = await api.GetScheduledAsync();
            var rows = new List<BillRow>();
            foreach (var s in sched.EnumerateArray())
            {
                var id = s.GetProperty("id").GetInt32();
                var active = s.TryGetProperty("active", out var ac) && ac.GetBoolean();
                var title = $"{JsonUi.Str(s, "name")} · {JsonUi.Str(s, "kind")} · {JsonUi.Money(s, "amount")}";
                var sub =
                    $"{JsonUi.Str(s, "profile_name")} · {JsonUi.Str(s, "account_nickname", "no account")} · " +
                    $"next {JsonUi.Str(s, "next_date")} · {JsonUi.Str(s, "cadence")} · {JsonUi.Str(s, "certainty")}";
                if (!active) sub += " · ENDED";
                rows.Add(new BillRow(id, title, sub, active ? Visibility.Visible : Visibility.Collapsed));
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

    private void FillAccounts()
    {
        AccountBox.Items.Clear();
        if (ProfileBox.SelectedItem is not ComboBoxItem pi || pi.Tag is not int profileId)
            return;
        if (_accounts.ValueKind != JsonValueKind.Array)
            return;
        foreach (var a in _accounts.EnumerateArray())
        {
            if (a.GetProperty("profile_id").GetInt32() != profileId) continue;
            var id = a.GetProperty("id").GetInt32();
            var label = $"{JsonUi.Str(a, "nickname")} [{JsonUi.Str(a, "kind")}]";
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
            var kind = "expense";
            if (KindBox.SelectedItem is ComboBoxItem ki && ki.Tag is string ks) kind = ks;
            int? accountId = null;
            if (AccountBox.SelectedItem is ComboBoxItem ai && ai.Tag is int aid)
                accountId = aid;
            if (kind == "expense" && accountId is null)
                throw new InvalidOperationException("Expenses require an account/card.");

            var cadence = "monthly";
            if (CadenceBox.SelectedItem is ComboBoxItem ci && ci.Tag is string cs) cadence = cs;
            var certainty = "fixed";
            if (CertaintyBox.SelectedItem is ComboBoxItem cei && cei.Tag is string ces) certainty = ces;

            var next = NextDateBox.Date?.Date ?? DateTime.Today;

            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = profileId,
                ["name"] = NameBox.Text?.Trim() ?? "Recurring",
                ["amount"] = double.IsNaN(AmtBox.Value) ? 0 : (decimal)AmtBox.Value,
                ["next_date"] = next.ToString("yyyy-MM-dd"),
                ["cadence"] = cadence,
                ["certainty"] = certainty,
                ["kind"] = kind,
                ["account_id"] = accountId,
                ["active"] = true,
            };

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateScheduledAsync(body);
            MsgText.Text = "Saved.";
            NameBox.Text = "";
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

    private sealed record BillRow(int Id, string Title, string Subtitle, Visibility EndVisible);
}
