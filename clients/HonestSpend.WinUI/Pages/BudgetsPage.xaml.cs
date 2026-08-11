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
            var sug = await api.GetBudgetSuggestionsAsync(AppState.SelectedProfileId);
            ApplySuggestions(sug);
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
        CutPanel.Children.Clear();
        var n = 0;
        if (cuts.TryGetProperty("offers", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var o in arr.EnumerateArray())
            {
                var ruleId = JsonUi.Int(o, "budget_rule_id", 0);
                var kind = JsonUi.Str(o, "kind");
                if (ruleId <= 0)
                    continue;
                var dict = new Dictionary<string, object?>();
                if (o.TryGetProperty("params", out var pr) && pr.ValueKind == JsonValueKind.Object)
                {
                    foreach (var prop in pr.EnumerateObject())
                    {
                        dict[prop.Name] = prop.Value.ValueKind switch
                        {
                            JsonValueKind.Number when prop.Value.TryGetInt32(out var i) => i,
                            JsonValueKind.Number => prop.Value.GetDouble(),
                            JsonValueKind.String => prop.Value.GetString(),
                            _ => prop.Value.GetRawText(),
                        };
                    }
                }
                var btn = new Button
                {
                    Content = $"{JsonUi.Str(o, "label")} · free ${JsonUi.Str(o, "free_amount")}",
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Tag = (ruleId, kind, dict),
                };
                btn.Click += async (_, _) =>
                {
                    try
                    {
                        using var api = new LedgerApiClient();
                        await api.EnsureBackendAsync();
                        await api.ApplyBudgetCutAsync(ruleId, kind, dict, "Applied from Budgets");
                        MsgBar.Title = "Cut applied";
                        MsgBar.Message = "Safe to spend reserve updated.";
                        MsgBar.Severity = InfoBarSeverity.Success;
                        MsgBar.IsOpen = true;
                        await LoadAsync();
                    }
                    catch (Exception ex)
                    {
                        MsgBar.Message = ex.Message;
                        MsgBar.Severity = InfoBarSeverity.Error;
                        MsgBar.IsOpen = true;
                    }
                };
                CutPanel.Children.Add(btn);
                if (++n >= 8)
                    break;
            }
        }
        if (n == 0)
            CutPanel.Children.Add(new TextBlock { Text = "No cut offers (add budgets with remaining first).", Opacity = 0.7 });
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
            ApplySuggestions(sug);
        }
        catch (Exception ex)
        {
            MsgBar.Message = ex.Message;
            MsgBar.IsOpen = true;
        }
    }

    private void ApplySuggestions(JsonElement sug)
    {
        SuggestPanel.Children.Clear();
        var n = 0;
        if (sug.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var it in arr.EnumerateArray())
            {
                var catId = JsonUi.Int(it, "category_id", 0);
                var period = JsonUi.Str(it, "period");
                var suggested = JsonUi.Str(it, "suggested_amount", "0");
                var current = JsonUi.Str(it, "current_amount", "—");
                var name = JsonUi.Str(it, "category_name");
                var trend = JsonUi.Str(it, "trend");
                var window = JsonUi.Str(it, "window");
                if (catId <= 0 || string.IsNullOrEmpty(period))
                    continue;

                var row = new StackPanel { Spacing = 4, Margin = new Thickness(0, 0, 0, 8) };
                row.Children.Add(new TextBlock
                {
                    Text =
                        $"{name} · {period}: suggest ${suggested} " +
                        $"(now {current}) · {window} · trend {trend}",
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.9,
                });
                var accept = new Button
                {
                    Content = $"Accept ${suggested}",
                    Style = (Style)Application.Current.Resources["AccentButtonStyle"],
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Tag = (catId, period, suggested, name),
                };
                accept.Click += AcceptSuggestion_Click;
                row.Children.Add(accept);
                SuggestPanel.Children.Add(row);
                if (++n >= 15)
                    break;
            }
        }
        if (n == 0)
        {
            SuggestPanel.Children.Add(new TextBlock
            {
                Text = "No suggestions yet — categorize spend history, then refresh.",
                Opacity = 0.7,
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private async void AcceptSuggestion_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ValueTuple<int, string, string, string> tag)
            return;
        var (catId, period, suggestedStr, name) = tag;
        if (!decimal.TryParse(suggestedStr, out var amt))
            return;
        try
        {
            var pid = AppState.SelectedProfileId ?? 1;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.AcceptBudgetSuggestionAsync(pid, catId, period, amt, name);
            MsgBar.Title = "Accepted";
            MsgBar.Message = $"Set {name} {period} plan to ${amt:0.00} from history.";
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

    private async void FillSuggestIntoForm_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (CategoryBox.SelectedIndex < 0 || CategoryBox.SelectedIndex >= _categories.Count)
            {
                MsgBar.Message = "Pick a category first.";
                MsgBar.IsOpen = true;
                return;
            }
            var catId = _categories[CategoryBox.SelectedIndex].Id;
            var period = "monthly";
            if (PeriodBox.SelectedItem is ComboBoxItem cbi && cbi.Tag is string tag)
                period = tag;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var sug = await api.GetBudgetSuggestionsAsync(AppState.SelectedProfileId);
            if (sug.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var it in arr.EnumerateArray())
                {
                    if (JsonUi.Int(it, "category_id", 0) == catId
                        && JsonUi.Str(it, "period") == period)
                    {
                        AmountBox.Text = JsonUi.Str(it, "suggested_amount");
                        MsgBar.Message = $"Filled ${AmountBox.Text} from {JsonUi.Str(it, "window")} average.";
                        MsgBar.IsOpen = true;
                        return;
                    }
                }
            }
            // no matching row — still try accept path for suggestion engine via create with period
            MsgBar.Message = "No matching suggestion row; save after entering an amount, or refresh suggestions.";
            MsgBar.IsOpen = true;
        }
        catch (Exception ex)
        {
            MsgBar.Message = ex.Message;
            MsgBar.IsOpen = true;
        }
    }
}
