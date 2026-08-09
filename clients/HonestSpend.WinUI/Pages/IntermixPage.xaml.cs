using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class IntermixPage : Page
{
    private readonly List<Acct> _accts = new();

    public IntermixPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAccountsAsync();
    }

    private async Task LoadAccountsAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var accounts = await api.GetAccountsAsync();
            var profiles = await api.GetProfilesAsync();
            var pmap = new Dictionary<int, string>();
            var pslug = new Dictionary<int, string>();
            foreach (var p in profiles.EnumerateArray())
            {
                var id = p.GetProperty("id").GetInt32();
                pmap[id] = JsonUi.Str(p, "display_name");
                pslug[id] = JsonUi.Str(p, "slug");
            }

            _accts.Clear();
            FromBox.Items.Clear();
            ToBox.Items.Clear();
            foreach (var a in accounts.EnumerateArray())
            {
                var id = a.GetProperty("id").GetInt32();
                var pid = a.GetProperty("profile_id").GetInt32();
                var kind = JsonUi.Str(a, "kind");
                var nick = JsonUi.Str(a, "nickname");
                var label = $"{nick} · {pmap.GetValueOrDefault(pid, "?")} · {kind}";
                var row = new Acct(id, pid, kind, nick, pslug.GetValueOrDefault(pid, ""), label);
                _accts.Add(row);
                FromBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
                ToBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
            }
            if (FromBox.Items.Count > 0) FromBox.SelectedIndex = 0;
            if (ToBox.Items.Count > 1) ToBox.SelectedIndex = 1;
            else if (ToBox.Items.Count > 0) ToBox.SelectedIndex = 0;
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void SelectKind(string tag)
    {
        for (var i = 0; i < KindBox.Items.Count; i++)
        {
            if (KindBox.Items[i] is ComboBoxItem cbi && cbi.Tag as string == tag)
            {
                KindBox.SelectedIndex = i;
                return;
            }
        }
    }

    private void SelectAccount(ComboBox box, Func<Acct, bool> pred)
    {
        var hit = _accts.FirstOrDefault(pred);
        if (hit is null) return;
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem cbi && cbi.Tag is int id && id == hit.Id)
            {
                box.SelectedIndex = i;
                return;
            }
        }
    }

    private void Play_Reimburse(object sender, RoutedEventArgs e)
    {
        SelectKind("reimburse");
        // Biz cash out → personal cash/card in (from=business checking, to=personal)
        SelectAccount(FromBox, a => a.Kind is "checking" or "savings" && a.Slug != "personal");
        SelectAccount(ToBox, a => a.Slug == "personal");
        MemoBox.Text = "Reimburse personal charge from business";
        PlaybookHint.Text =
            "Reimburse: business pays personal for a business expense paid personally. " +
            "Keeps books clean — not an owner distribution.";
        AmtBox.Value = 100;
    }

    private void Play_Dist(object sender, RoutedEventArgs e)
    {
        SelectKind("distribution");
        SelectAccount(FromBox, a => a.Kind is "checking" or "savings" && a.Slug != "personal");
        SelectAccount(ToBox, a => a.Slug == "personal" && a.Kind is "checking" or "savings");
        MemoBox.Text = "Owner distribution (quarterly)";
        PlaybookHint.Text =
            "Distribution: equity transfer biz → personal. S-corp: track separately from W-2 wages; " +
            "confirm with your CPA. Not tax advice.";
        AmtBox.Value = 1000;
    }

    private void Play_Inject(object sender, RoutedEventArgs e)
    {
        SelectKind("capital_inject");
        SelectAccount(FromBox, a => a.Slug == "personal" && a.Kind is "checking" or "savings");
        SelectAccount(ToBox, a => a.Kind is "checking" or "savings" && a.Slug != "personal");
        MemoBox.Text = "Owner capital contribution";
        PlaybookHint.Text =
            "Capital inject: personal → business equity. Use when funding ops — not income to the entity.";
        AmtBox.Value = 500;
    }

    private void Play_Allowance(object sender, RoutedEventArgs e)
    {
        SelectKind("child_allowance");
        SelectAccount(FromBox, a => a.Slug == "personal" && a.Kind is "checking" or "savings");
        SelectAccount(ToBox, a => a.Kind is "checking" or "savings" && a.Slug != "personal");
        MemoBox.Text = "Weekly / monthly allowance";
        PlaybookHint.Text =
            "Child allowance: personal → child entity transfer. Siloed Spendable for the child; not a business expense.";
        AmtBox.Value = 20;
    }

    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var kind = "reimburse";
            if (KindBox.SelectedItem is ComboBoxItem k && k.Tag is string ks) kind = ks;
            if (FromBox.SelectedItem is not ComboBoxItem f || f.Tag is not int fromId)
                throw new InvalidOperationException("Pick from account.");
            if (ToBox.SelectedItem is not ComboBoxItem t || t.Tag is not int toId)
                throw new InvalidOperationException("Pick to account.");

            var body = new
            {
                kind,
                amount = double.IsNaN(AmtBox.Value) ? 0m : (decimal)AmtBox.Value,
                from_account_id = fromId,
                to_account_id = toId,
                memo = string.IsNullOrWhiteSpace(MemoBox.Text) ? null : MemoBox.Text.Trim(),
            };

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.IntermixAsync(body);
            ResultText.Text =
                $"{JsonUi.Str(res, "label")}: {JsonUi.Money(res, "amount")}\n" +
                $"{JsonUi.Str(res, "guidance")}";
            await LoadGraphAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Graph_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await LoadGraphAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task LoadGraphAsync(LedgerApiClient api)
    {
        var g = await api.GetIntermixGraphAsync(365);
        GraphMsg.Text = JsonUi.Str(g, "message");
        var lines = new List<string>();
        if (g.TryGetProperty("edges", out var edges) && edges.ValueKind == JsonValueKind.Array)
        {
            foreach (var e in edges.EnumerateArray())
            {
                lines.Add(
                    $"{JsonUi.Str(e, "from_name")} → {JsonUi.Str(e, "to_name")}: " +
                    $"${JsonUi.Str(e, "total")} ({JsonUi.Str(e, "count")} moves)");
            }
        }
        if (lines.Count == 0) lines.Add("No edges yet.");
        GraphList.ItemsSource = lines;
    }

    private sealed record Acct(int Id, int ProfileId, string Kind, string Nick, string Slug, string Label);
}
