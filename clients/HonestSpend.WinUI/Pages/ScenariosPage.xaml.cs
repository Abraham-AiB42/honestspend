using System.Collections.ObjectModel;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class ScenariosPage : Page
{
    private readonly ObservableCollection<ScenarioRow> _rows = new();

    public ScenariosPage()
    {
        InitializeComponent();
        ScenarioList.ItemsSource = _rows;
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
            var res = await api.ListScenariosAsync();
            _rows.Clear();
            if (res.TryGetProperty("scenarios", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var s in arr.EnumerateArray())
                {
                    var id = s.GetProperty("id").GetInt32();
                    var name = JsonUi.Str(s, "name");
                    var n = 0;
                    if (s.TryGetProperty("extra_outflows", out var ex) && ex.ValueKind == JsonValueKind.Array)
                        n = ex.GetArrayLength();
                    _rows.Add(new ScenarioRow(id, name, $"{n} outflow(s) · {JsonUi.Str(s, "scope")} · {JsonUi.Str(s, "created_at")}"));
                }
            }
            StatusText.Text = _rows.Count == 0
                ? "No scenarios yet — save one below or from Can I buy?"
                : $"{_rows.Count} saved.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Quick_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var amt = double.IsNaN(AmtBox.Value) ? 0m : (decimal)AmtBox.Value;
            if (amt <= 0) throw new InvalidOperationException("Enter an amount.");
            var name = string.IsNullOrWhiteSpace(NameBox.Text) ? $"Buy ${amt:0.00}" : NameBox.Text.Trim();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.QuickScenarioAsync(new
            {
                name,
                amount = amt,
                on_date = DateTime.Today.ToString("yyyy-MM-dd"),
                profile_id = AppState.SelectedProfileId,
                scope = AppState.IfppScope,
            });
            if (res.TryGetProperty("simulation", out var sim))
                ResultText.Text = JsonUi.Str(sim, "message");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Run_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ScenarioRow row) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.RunScenarioAsync(row.Id);
            if (res.TryGetProperty("simulation", out var sim))
                ResultText.Text = $"[{row.Title}] {JsonUi.Str(sim, "message")}";
            else
                ResultText.Text = res.GetRawText();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Delete_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ScenarioRow row) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.DeleteScenarioAsync(row.Id);
            ResultText.Text = $"Deleted · {row.Title}";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    public sealed class ScenarioRow
    {
        public int Id { get; }
        public string Title { get; }
        public string Subtitle { get; }
        public ScenarioRow(int id, string title, string subtitle)
        {
            Id = id;
            Title = title;
            Subtitle = subtitle;
        }
    }
}
