using System.Diagnostics;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class PlaidPage : Page
{
    private string _linkUrl = "http://127.0.0.1:7420/static/plaid-link.html";

    public PlaidPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            var st = await api.GetPlaidStatusAsync();
            var enabled = st.TryGetProperty("enabled", out var en) && en.GetBoolean();
            StatusText.Text = enabled
                ? $"Plaid ON · env {JsonUi.Str(st, "env")}"
                : "Plaid OFF — CSV import still works.";
            HintText.Text = JsonUi.Str(st, "hint") + " " + JsonUi.Str(st, "sandbox_hint", "");
            _linkUrl = JsonUi.Str(st, "link_url", _linkUrl);
            LinkUrlText.Text = _linkUrl;

            var profiles = await api.GetProfilesAsync();
            ProfileBox.Items.Clear();
            foreach (var p in profiles.EnumerateArray())
            {
                ProfileBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(p, "display_name")} ({JsonUi.Str(p, "slug")})",
                    Tag = p.GetProperty("id").GetInt32(),
                });
            }
            if (ProfileBox.Items.Count > 0) ProfileBox.SelectedIndex = 0;

            var items = await api.GetPlaidItemsAsync();
            var rows = new List<ItemRow>();
            if (items.ValueKind == JsonValueKind.Array)
            {
                foreach (var it in items.EnumerateArray())
                {
                    var id = it.TryGetProperty("id", out var idEl) ? idEl.GetInt32() : JsonUi.Int(it, "plaid_item_id");
                    var itemId = JsonUi.Str(it, "item_id", "");
                    if (itemId.Length > 24) itemId = itemId[..24] + "…";
                    var age = JsonUi.Str(it, "sync_age_hours", "—");
                    var reauth = it.TryGetProperty("needs_reauth", out var nr) && nr.GetBoolean();
                    rows.Add(new ItemRow(
                        id,
                        JsonUi.Str(it, "institution_name", JsonUi.Str(it, "institution", "Bank")),
                        $"{JsonUi.Str(it, "status")}" +
                        (reauth ? " · needs re-auth" : "") +
                        $" · {JsonUi.Str(it, "accounts")} accounts · " +
                        $"synced {JsonUi.Str(it, "last_synced_at", "never")} ({age}h)"));
                }
            }
            ItemList.ItemsSource = rows;
            MsgText.Text = rows.Count == 0
                ? "No linked institutions yet."
                : $"{rows.Count} linked item(s).";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private int? SelectedProfileId()
    {
        if (ProfileBox.SelectedItem is ComboBoxItem cbi && cbi.Tag is int id)
            return id;
        return null;
    }

    private void OpenLink_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var url = _linkUrl;
            var pid = SelectedProfileId();
            if (pid is not null)
            {
                url += (url.Contains('?') ? "&" : "?") + "profile_id=" + pid.Value;
            }
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            MsgText.Text = "Browser opened for Plaid Link. When done, hit Refresh here.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Sandbox_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick an entity.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PlaidSandboxLinkAsync(pid);
            MsgText.Text =
                $"Sandbox linked · {JsonUi.Str(res, "institution")}\n" +
                (res.TryGetProperty("sync", out var s) ? s.GetRawText() : "");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Exchange_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick an entity.");
            if (string.IsNullOrWhiteSpace(PublicTokenBox.Text))
                throw new InvalidOperationException("Paste public_token.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PlaidExchangeAsync(new
            {
                public_token = PublicTokenBox.Text.Trim(),
                profile_id = pid,
                institution_name = string.IsNullOrWhiteSpace(InstNameBox.Text) ? null : InstNameBox.Text.Trim(),
            });
            MsgText.Text = "Exchanged · " + res.GetRawText();
            PublicTokenBox.Text = "";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Sync_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PlaidSyncAsync(id);
            MsgText.Text = "Sync: " + res.GetRawText();
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Disconnect_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        var confirm = new ContentDialog
        {
            Title = "Disconnect bank?",
            Content = "Clears access token. Local accounts stay unless you choose otherwise later.",
            PrimaryButtonText = "Disconnect",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await confirm.ShowAsync() != ContentDialogResult.Primary) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PlaidDisconnectAsync(id, keepAccounts: true);
            MsgText.Text = $"Disconnected · unlinked accounts {JsonUi.Str(res, "accounts_unlinked")}";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private sealed record ItemRow(int Id, string Title, string Subtitle);
}
