using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class UsersPage : Page
{
    public UsersPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            var me = await api.GetPermissionMeAsync();
            var multi = me.TryGetProperty("multi_user_mode", out var mu) && mu.ValueKind == JsonValueKind.True;
            MeText.Text =
                $"{JsonUi.Str(me, "display_name")} (@{JsonUi.Str(me, "username")}) · role {JsonUi.Str(me, "role")}" +
                (multi ? " · MULTI-USER (API key required)" : " · single-user local");
            var caps = new List<string>();
            if (me.TryGetProperty("capabilities", out var ca) && ca.ValueKind == JsonValueKind.Array)
            {
                foreach (var c in ca.EnumerateArray())
                    caps.Add("• " + (c.GetString() ?? c.GetRawText()));
            }
            if (me.TryGetProperty("hint", out var hint) && hint.ValueKind == JsonValueKind.String)
                caps.Insert(0, hint.GetString() ?? "");
            CapList.ItemsSource = caps;

            var roles = await api.GetPermissionRolesAsync();
            var roleLines = new List<string>();
            if (roles.TryGetProperty("roles", out var ra) && ra.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in ra.EnumerateArray())
                {
                    roleLines.Add(
                        $"{JsonUi.Str(r, "role")}: {JsonUi.Str(r, "description")} · " +
                        string.Join(", ", r.TryGetProperty("capabilities", out var caps2) && caps2.ValueKind == JsonValueKind.Array
                            ? caps2.EnumerateArray().Select(x => x.GetString()).Where(x => x is not null)
                            : Array.Empty<string>()));
                }
            }
            RoleList.ItemsSource = roleLines;

            var users = await api.GetPermissionUsersAsync();
            var rows = new List<UserRow>();
            var arr = users.ValueKind == JsonValueKind.Array
                ? users
                : (users.TryGetProperty("users", out var ua) ? ua : default);
            if (arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var u in arr.EnumerateArray())
                {
                    rows.Add(new UserRow(
                        u.GetProperty("id").GetInt32(),
                        $"{JsonUi.Str(u, "display_name")} (@{JsonUi.Str(u, "username")})",
                        $"role {JsonUi.Str(u, "role")} · active {JsonUi.Str(u, "active")}" +
                        (u.TryGetProperty("has_token", out var ht) && ht.ValueKind == JsonValueKind.True
                            ? " · has token"
                            : "")));
                }
            }
            UserList.ItemsSource = rows;
            var multi2 = users.TryGetProperty("multi_user_mode", out var m2) && m2.ValueKind == JsonValueKind.True;
            MsgText.Text = multi2
                ? $"{rows.Count} users · multi-user ON — set X-API-Key in Settings"
                : $"{rows.Count} users";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Create_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        TokenText.Text = "";
        try
        {
            if (string.IsNullOrWhiteSpace(UserNameBox.Text))
                throw new InvalidOperationException("Username required.");
            var role = "viewer";
            if (RoleBox.SelectedItem is ComboBoxItem ri && ri.Tag is string rt)
                role = rt;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CreatePermissionUserAsync(new
            {
                username = UserNameBox.Text.Trim(),
                display_name = string.IsNullOrWhiteSpace(DisplayBox.Text) ? UserNameBox.Text.Trim() : DisplayBox.Text.Trim(),
                role,
                issue_token = IssueTokenBox.IsChecked == true,
            });
            if (res.TryGetProperty("api_token", out var tok) && tok.ValueKind == JsonValueKind.String)
            {
                TokenText.Text =
                    $"API token (copy now — shown once):\n{tok.GetString()}\n\n{JsonUi.Str(res, "hint")}";
            }
            MsgText.Text = $"Created user #{JsonUi.Str(res, "id")}";
            UserNameBox.Text = "";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Rotate_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.RotateTokenAsync(id);
            TokenText.Text =
                $"New token for {JsonUi.Str(res, "username")}:\n{JsonUi.Str(res, "api_token")}\n\nStore it — not shown again.";
            MsgText.Text = "Token rotated.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private sealed record UserRow(int Id, string Title, string Subtitle);
}
