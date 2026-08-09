using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class AuditPage : Page
{
    public AuditPage()
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
            var limit = double.IsNaN(LimitBox.Value) ? 50 : (int)LimitBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.GetAuditAsync(limit);
            var rows = new List<EventRow>();
            // API may return {events:[...]} or bare array
            JsonElement arr = default;
            if (res.ValueKind == JsonValueKind.Array)
                arr = res;
            else if (res.TryGetProperty("events", out var e) && e.ValueKind == JsonValueKind.Array)
                arr = e;
            else if (res.TryGetProperty("items", out var it) && it.ValueKind == JsonValueKind.Array)
                arr = it;

            if (arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var ev in arr.EnumerateArray())
                {
                    var when = JsonUi.Str(ev, "created_at", JsonUi.Str(ev, "at", ""));
                    var user = JsonUi.Str(ev, "username", "owner");
                    var role = JsonUi.Str(ev, "role", "");
                    var action = JsonUi.Str(ev, "action", "");
                    var path = JsonUi.Str(ev, "path", "");
                    var detail = JsonUi.Str(ev, "detail", "");
                    var title = string.IsNullOrEmpty(when) || when == "—"
                        ? $"{user}: {action}"
                        : $"{when} · {user}: {action}";
                    var sub = string.Join(" · ", new[] { role, path, detail }.Where(s => !string.IsNullOrEmpty(s) && s != "—"));
                    rows.Add(new EventRow(title, sub));
                }
            }
            EventList.ItemsSource = rows;
            StatusText.Text = rows.Count == 0
                ? "No audit events yet — activity appears as you use multi-user / writes."
                : $"{rows.Count} event(s).";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            StatusText.Text = "Could not load audit log.";
        }
    }

    private sealed record EventRow(string Title, string Subtitle);
}
