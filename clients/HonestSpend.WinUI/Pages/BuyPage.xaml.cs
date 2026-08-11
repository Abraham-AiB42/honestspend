using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class BuyPage : Page
{
    private readonly List<(int Id, string Name)> _categories = new();

    public BuyPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadCategoriesAsync();
    }

    private async Task LoadCategoriesAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var cats = await api.GetCategoriesAsync();
            _categories.Clear();
            CategoryBox.Items.Clear();
            CategoryBox.Items.Add("(Any category)");
            _categories.Add((0, "(Any)"));
            if (cats.ValueKind == JsonValueKind.Array)
            {
                foreach (var c in cats.EnumerateArray())
                {
                    var id = JsonUi.Int(c, "id", 0);
                    var name = JsonUi.Str(c, "display_name");
                    if (id <= 0 || string.IsNullOrEmpty(name) || name == "—")
                        continue;
                    _categories.Add((id, name));
                    CategoryBox.Items.Add(name);
                }
            }
            CategoryBox.SelectedIndex = 0;
        }
        catch
        {
            /* optional */
        }
    }

    private async void Check_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        BudgetCheckText.Text = "";
        CutPanel.Children.Clear();
        try
        {
            if (App.Backend is not null)
                await App.Backend.EnsureRunningAsync();

            var prefer = "auto";
            if (PreferBox.SelectedItem is ComboBoxItem item && item.Tag is string t)
                prefer = t;

            var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
            using var api = new LedgerApiClient();
            var res = await api.PrePurchaseAsync(amount, prefer);
            var verdict = res.GetProperty("verdict").GetString() ?? "";
            VerdictText.Text = verdict switch
            {
                "safe" => "Yes — safe",
                "safe_via_other_method" => "Yes — use the other method",
                _ => "No — don't buy yet",
            };

            var rec = res.GetProperty("recommended");
            RecText.Text =
                $"Use: {UiCopy.PayMethod(JsonUi.Str(rec, "method"))} · {JsonUi.Str(rec, "account_name")}";
            ReasonText.Text = JsonUi.Str(rec, "reason");
            if (rec.TryGetProperty("remaining_after", out var rem) && rem.ValueKind != JsonValueKind.Null)
                ReasonText.Text += $"\nSafe to spend after: {JsonUi.Money(rec, "remaining_after")}";

            // Category budget remaining check
            await ApplyCategoryBudgetCheckAsync(api, amount);

            var opts = new List<string>();
            if (res.TryGetProperty("options", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in arr.EnumerateArray())
                {
                    var safe = o.TryGetProperty("safe", out var sf) && sf.GetBoolean();
                    opts.Add(
                        $"{(safe ? "✓" : "✗")} {UiCopy.PayMethod(JsonUi.Str(o, "method"))} · {JsonUi.Str(o, "account_name")} — " +
                        JsonUi.Str(o, "reason"));
                }
            }
            OptionsList.ItemsSource = opts.Count > 0 ? opts : new List<string> { "No alternate options." };
            ScopeText.Text =
                $"{UiCopy.MoneyView(AppState.IfppScope)}" +
                $" · as of {JsonUi.Str(res, "as_of")}";

            // Always show cut offers when purchase is tight or category short
            var tight = verdict is not ("safe" or "safe_via_other_method");
            await LoadCutOffersAsync(api, force: tight || BudgetCheckText.Text.Contains("short", StringComparison.OrdinalIgnoreCase));
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task ApplyCategoryBudgetCheckAsync(LedgerApiClient api, decimal amount)
    {
        if (CategoryBox.SelectedIndex <= 0 || CategoryBox.SelectedIndex >= _categories.Count)
        {
            BudgetCheckText.Text = "";
            return;
        }
        var catId = _categories[CategoryBox.SelectedIndex].Id;
        var catName = _categories[CategoryBox.SelectedIndex].Name;
        var st = await api.GetBudgetStatusAsync(AppState.SelectedProfileId);
        if (!st.TryGetProperty("items", out var items) || items.ValueKind != JsonValueKind.Array)
        {
            BudgetCheckText.Text = $"No budget rule for {catName} — only Safe to spend applies.";
            return;
        }
        JsonElement? match = null;
        foreach (var it in items.EnumerateArray())
        {
            if (JsonUi.Int(it, "category_id", 0) == catId)
            {
                match = it;
                // prefer daily if multiple
                if (JsonUi.Str(it, "period") == "daily")
                    break;
            }
        }
        if (match is null)
        {
            BudgetCheckText.Text = $"No budget for {catName}. Consider adding one under Budgets.";
            return;
        }
        var m = match.Value;
        var rem = 0m;
        decimal.TryParse(JsonUi.Str(m, "remaining", "0"), out rem);
        var plan = JsonUi.Str(m, "plan");
        var period = JsonUi.Str(m, "period");
        var status = JsonUi.Str(m, "status");
        if (amount > rem)
        {
            BudgetCheckText.Text =
                $"Budget short: {catName} ({period}) has ${rem:0.00} left of ${plan}. " +
                $"This purchase needs ${amount:0.00}. Cut a budget or wait for the next period.";
            if (status != "over")
                VerdictText.Text = VerdictText.Text.StartsWith("Yes")
                    ? "Maybe — cash ok, budget tight"
                    : VerdictText.Text;
        }
        else
        {
            BudgetCheckText.Text =
                $"Budget ok: {catName} ({period}) has ${rem:0.00} left of ${plan} after this would still clear.";
        }
    }

    private async Task LoadCutOffersAsync(LedgerApiClient api, bool force)
    {
        CutPanel.Children.Clear();
        if (!force)
            return;
        try
        {
            var cuts = await api.GetBudgetCutsAsync(AppState.SelectedProfileId);
            if (!cuts.TryGetProperty("offers", out var arr) || arr.ValueKind != JsonValueKind.Array)
                return;
            var n = 0;
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
                        await api.ApplyBudgetCutAsync(ruleId, kind, dict, "Applied from Can I buy?");
                        ScenarioMsg.Text = "Cut applied — re-check purchase.";
                        Check_Click(btn, new RoutedEventArgs());
                    }
                    catch (Exception ex)
                    {
                        ErrorBar.Message = ex.Message;
                        ErrorBar.IsOpen = true;
                    }
                };
                CutPanel.Children.Add(btn);
                if (++n >= 4)
                    break;
            }
        }
        catch
        {
            /* optional */
        }
    }

    private async void Simulate_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.SimulateIfppAsync(new
            {
                extra_outflows = new[]
                {
                    new
                    {
                        amount,
                        name = "What-if purchase",
                        on_date = DateTime.Today.ToString("yyyy-MM-dd"),
                    },
                },
                profile_id = AppState.SelectedProfileId,
                scope = AppState.IfppScope,
            });
            SimText.Text = JsonUi.Str(res, "message");
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void SaveScenario_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
            if (amount <= 0)
                throw new InvalidOperationException("Enter an amount first.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.QuickScenarioAsync(new
            {
                name = $"Buy ${amount:0.00}",
                amount,
                on_date = DateTime.Today.ToString("yyyy-MM-dd"),
                profile_id = AppState.SelectedProfileId,
                scope = AppState.IfppScope,
            });
            var sim = res.TryGetProperty("simulation", out var s) ? s : default;
            ScenarioMsg.Text =
                $"Saved scenario · {JsonUi.Str(res.GetProperty("scenario"), "name")}. " +
                (sim.ValueKind == JsonValueKind.Object ? JsonUi.Str(sim, "message") : "");
            if (sim.ValueKind == JsonValueKind.Object)
                SimText.Text = JsonUi.Str(sim, "message");
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
