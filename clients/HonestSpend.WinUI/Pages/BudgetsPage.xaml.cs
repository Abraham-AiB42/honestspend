using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class BudgetsPage : Page
{
    private readonly List<(int Id, string Name)> _categories = new();

    public BudgetsPage()
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
        MsgBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await LoadCategoriesAsync(api);
            var st = await api.GetBudgetStatusAsync(AppState.SelectedProfileId);
            ApplyStatus(st);
            var cuts = await api.GetBudgetCutsAsync(AppState.SelectedProfileId);
            ApplyCuts(cuts);
        }
        catch (Exception ex)
        {
            MsgBar.Title = "Error";
            MsgBar.Message = ex.Message;
            MsgBar.Severity = InfoBarSeverity.Error;
            MsgBar.IsOpen = true;
        }
    }

    private async Task LoadCategoriesAsync(LedgerApiClient api)
    {
        _categories.Clear();
        CategoryBox.Items.Clear();
        try
        {
            var cats = await api.GetCategoriesAsync();
            if (cats.ValueKind != JsonValueKind.Array)
                return;
            foreach (var c in cats.EnumerateArray())
            {
                var id = JsonUi.Int(c, "id", 0);
                var name = JsonUi.Str(c, "display_name");
                if (id <= 0 || string.IsNullOrEmpty(name) || name == "—")
                    continue;
                _categories.Add((id, name));
                CategoryBox.Items.Add(name);
            }
            if (CategoryBox.Items.Count > 0)
                CategoryBox.SelectedIndex = 0;
        }
        catch
        {
            /* optional */
        }
    }

    private void ApplyStatus(JsonElement st)
    {
        var reserve = JsonUi.Str(st, "reserve_total", "0");
        var enabled = st.TryGetProperty("reserve_enabled", out var re) && re.ValueKind == JsonValueKind.True;
        ReserveText.Text = enabled
            ? $"${reserve} reserved from category budgets (included in Safe to spend math)."
            : "Budget reserve is off in settings.";

        DailyList.ItemsSource = Lines(st, "daily");
        WeeklyList.ItemsSource = Lines(st, "weekly");
        MonthlyList.ItemsSource = Lines(st, "monthly");
    }

    private static List<string> Lines(JsonElement st, string period)
    {
        var list = new List<string>();
        if (!st.TryGetProperty("by_period", out var bp) || bp.ValueKind != JsonValueKind.Object)
            return list;
        if (!bp.TryGetProperty(period, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return list;
        foreach (var it in arr.EnumerateArray())
        {
            var name = JsonUi.Str(it, "name");
            var plan = JsonUi.Str(it, "plan");
            var actual = JsonUi.Str(it, "actual");
            var rem = JsonUi.Str(it, "remaining");
            var status = JsonUi.Str(it, "status");
            var win = JsonUi.Str(it, "window_label");
            list.Add($"{name}: plan ${plan} · spent ${actual} · left ${rem} · {status} ({win})");
        }
        if (list.Count == 0)
            list.Add($"No {period} budgets yet.");
        return list;
    }

    private void ApplyCuts(JsonElement cuts)
    {
        var list = new List<string>();
        if (cuts.TryGetProperty("offers", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var o in arr.EnumerateArray())
            {
                list.Add($"{JsonUi.Str(o, "label")} → free ${JsonUi.Str(o, "free_amount")}");
            }
        }
        if (list.Count == 0)
            list.Add("No cut offers (add budgets with remaining first).");
        CutList.ItemsSource = list;
    }

    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (CategoryBox.SelectedIndex < 0 || CategoryBox.SelectedIndex >= _categories.Count)
            {
                MsgBar.Message = "Pick a category.";
                MsgBar.IsOpen = true;
                return;
            }
            var catId = _categories[CategoryBox.SelectedIndex].Id;
            var period = "monthly";
            if (PeriodBox.SelectedItem is ComboBoxItem cbi && cbi.Tag is string tag)
                period = tag;
            if (!decimal.TryParse(AmountBox.Text?.Trim(), out var amt) || amt < 0)
            {
                MsgBar.Message = "Enter a valid plan amount.";
                MsgBar.IsOpen = true;
                return;
            }
            var pid = AppState.SelectedProfileId ?? 1;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateBudgetAsync(
                pid,
                catId,
                period,
                amt,
                string.IsNullOrWhiteSpace(NameBox.Text) ? null : NameBox.Text.Trim());
            MsgBar.Title = "Saved";
            MsgBar.Message = "Budget saved.";
            MsgBar.Severity = InfoBarSeverity.Success;
            MsgBar.IsOpen = true;
            await LoadAsync();
        }
        catch (Exception ex)
        {
            MsgBar.Title = "Error";
            MsgBar.Message = ex.Message;
            MsgBar.Severity = InfoBarSeverity.Error;
            MsgBar.IsOpen = true;
        }
    }

    private async void Suggest_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var sug = await api.GetBudgetSuggestionsAsync(AppState.SelectedProfileId);
            var list = new List<string>();
            if (sug.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var it in arr.EnumerateArray())
                {
                    list.Add(
                        $"{JsonUi.Str(it, "category_name")} {JsonUi.Str(it, "period")}: " +
                        $"suggest ${JsonUi.Str(it, "suggested_amount")} " +
                        $"(now ${JsonUi.Str(it, "current_amount")}) · {JsonUi.Str(it, "window")} · trend {JsonUi.Str(it, "trend")}");
                }
            }
            if (list.Count == 0)
                list.Add("No suggestions yet — add budgets and categorize spend history.");
            SuggestList.ItemsSource = list;
        }
        catch (Exception ex)
        {
            MsgBar.Message = ex.Message;
            MsgBar.IsOpen = true;
        }
    }
}
