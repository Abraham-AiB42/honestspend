using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class EntitiesPage : Page
{
    public EntitiesPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await RefreshAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async Task RefreshAsync()
    {
        ErrorBar.IsOpen = false;
        StatusText.Text = "Loading…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var profiles = await api.GetProfilesAsync();
            var lines = new List<string>();
            foreach (var p in profiles.EnumerateArray())
            {
                var et = UiCopy.EntityType(JsonUi.Str(p, "entity_type"));
                var tax = JsonUi.Str(p, "tax_form_primary", "");
                var taxBit = string.IsNullOrEmpty(tax) || tax == "—" || et == "Child" ? "" : $" · tax {tax}";
                lines.Add(
                    $"{JsonUi.Str(p, "display_name")} · {et}{taxBit}" +
                    (p.TryGetProperty("is_default", out var d) && d.ValueKind == JsonValueKind.True
                        ? " · default"
                        : ""));
            }
            if (lines.Count == 0) lines.Add("No entities yet.");
            EntityList.ItemsSource = lines;
            StatusText.Text = $"{lines.Count} entity(ies)";
        }
        catch (Exception ex)
        {
            StatusText.Text = "Error";
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void AddBiz_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var name = BizNameBox.Text?.Trim();
            if (string.IsNullOrEmpty(name))
                throw new InvalidOperationException("Enter a business display name.");
            var tax = "1120S";
            if (BizTaxBox.SelectedItem is ComboBoxItem ci && ci.Tag is string t)
                tax = t;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CreateProfileAsync(new
            {
                display_name = name,
                entity_type = "business",
                tax_form_primary = tax,
            });
            MsgText.Text = $"Added business · {JsonUi.Str(res, "display_name")}";
            BizNameBox.Text = "";
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void AddChild_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var name = ChildNameBox.Text?.Trim();
            if (string.IsNullOrEmpty(name))
                throw new InvalidOperationException("Enter a display name for the child entity.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var profiles = await api.GetProfilesAsync();
            int? personalId = null;
            foreach (var p in profiles.EnumerateArray())
            {
                if (JsonUi.Str(p, "slug") == "personal")
                {
                    personalId = p.GetProperty("id").GetInt32();
                    break;
                }
            }
            var res = await api.CreateProfileAsync(new
            {
                display_name = name,
                entity_type = "child",
                parent_profile_id = personalId,
            });
            MsgText.Text = $"Added child · {JsonUi.Str(res, "display_name")}";
            ChildNameBox.Text = "";
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
