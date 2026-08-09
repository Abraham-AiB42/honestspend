using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

/// <summary>Simple intermix playbooks — reimburse, pay yourself, fund biz, kid allowance.</summary>
public sealed partial class PlaybooksPage : Page
{
    public PlaybooksPage()
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
            var accts = await api.GetAccountsAsync();
            FromBox.Items.Clear();
            ToBox.Items.Clear();
            foreach (var a in accts.EnumerateArray())
            {
                if (a.TryGetProperty("archived_at", out var ar) && ar.ValueKind != JsonValueKind.Null)
                    continue;
                var id = a.GetProperty("id").GetInt32();
                var label =
                    $"{JsonUi.Str(a, "nickname")} · {UiCopy.AccountKind(JsonUi.Str(a, "kind"))}";
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

    private async void Go_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (FromBox.SelectedItem is not ComboBoxItem { Tag: int fromId })
                throw new InvalidOperationException("Pick a From account by name.");
            if (ToBox.SelectedItem is not ComboBoxItem { Tag: int toId })
                throw new InvalidOperationException("Pick a To account by name.");
            if (fromId == toId)
                throw new InvalidOperationException("From and To must be different accounts.");
            var kind = "reimburse";
            if (KindBox.SelectedItem is ComboBoxItem ki && ki.Tag is string k)
                kind = k;
            var amt = double.IsNaN(AmtBox.Value) ? 0m : (decimal)AmtBox.Value;
            if (amt <= 0)
                throw new InvalidOperationException("Amount must be greater than zero.");

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.IntermixAsync(new
            {
                kind,
                amount = amt,
                from_account_id = fromId,
                to_account_id = toId,
                memo = string.IsNullOrWhiteSpace(MemoBox.Text) ? null : MemoBox.Text.Trim(),
            });
            MsgText.Text =
                $"Recorded · {JsonUi.Str(res, "kind", kind)} · ${amt:0.00}. " +
                "Safe to spend updates per entity on Home.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
