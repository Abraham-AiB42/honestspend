using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class RulesPage : Page
{
    public RulesPage()
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

            try
            {
                var st = await api.GetCategorizerStatusAsync();
                StatusText.Text =
                    $"Categorizer · Grok {(st.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "on" : "off")} · " +
                    JsonUi.Str(st, "message", JsonUi.Str(st, "hint", ""));
            }
            catch
            {
                StatusText.Text = "Rules engine ready.";
            }

            var cats = await api.GetCategoriesAsync();
            CatBox.Items.Clear();
            foreach (var c in cats.EnumerateArray())
            {
                var name = JsonUi.Str(c, "display_name");
                var tax = JsonUi.Str(c, "tax_line", "");
                CatBox.Items.Add(new ComboBoxItem
                {
                    Content = string.IsNullOrEmpty(tax) || tax == "—" ? name : $"{name} [{tax}]",
                    Tag = c.GetProperty("id").GetInt32(),
                });
            }
            if (CatBox.Items.Count > 0) CatBox.SelectedIndex = 0;

            var rules = await api.GetRulesAsync();
            var rows = new List<RuleRow>();
            if (rules.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in rules.EnumerateArray())
                {
                    var id = r.GetProperty("id").GetInt32();
                    var title = $"{JsonUi.Str(r, "match_type")} · \"{JsonUi.Str(r, "pattern")}\" → {JsonUi.Str(r, "category_name")}";
                    var sub =
                        $"priority {JsonUi.Str(r, "priority")} · source {JsonUi.Str(r, "source")} · " +
                        $"active {JsonUi.Str(r, "active")}" +
                        (r.TryGetProperty("is_transfer", out var t) && t.GetBoolean() ? " · TRANSFER" : "");
                    rows.Add(new RuleRow(id, title, sub));
                }
            }
            RuleList.ItemsSource = rows;
            MsgText.Text = $"{rows.Count} rules";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Add_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (string.IsNullOrWhiteSpace(PatternBox.Text))
                throw new InvalidOperationException("Pattern required.");
            if (CatBox.SelectedItem is not ComboBoxItem ci || ci.Tag is not int catId)
                throw new InvalidOperationException("Pick a category.");
            var match = "contains";
            if (MatchBox.SelectedItem is ComboBoxItem mi && mi.Tag is string mt)
                match = mt;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateRuleAsync(new
            {
                match_type = match,
                pattern = PatternBox.Text.Trim(),
                category_id = catId,
                priority = double.IsNaN(PriorityBox.Value) ? 100 : (int)PriorityBox.Value,
                is_transfer = TransferBox.IsChecked == true,
                active = true,
            });
            PatternBox.Text = "";
            MsgText.Text = "Rule added.";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Delete_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.DeleteRuleAsync(id);
            MsgText.Text = "Deleted rule.";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private sealed record RuleRow(int Id, string Title, string Subtitle);
}
