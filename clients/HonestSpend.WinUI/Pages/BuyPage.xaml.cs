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
    private bool _busy;
    private bool _hasCash = true;
    private bool _raidMode;

    public BuyPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        SetIdleResult();
        await LoadCategoriesAsync();
        await RefreshBooksHintAsync();
    }

    private void SetIdleResult()
    {
        VerdictText.Text = "—";
        RecText.Text = "";
        ReasonText.Text = "Enter an amount and tap Check purchase.";
        BudgetCheckText.Text = "";
        OptionsList.ItemsSource = null;
        CutPanel.Children.Clear();
        ScopeText.Text = "";
        SimText.Text = "";
        ScenarioMsg.Text = "";
    }

    private async Task RefreshBooksHintAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var accts = await api.GetAccountsAsync();
            _hasCash = false;
            if (accts.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in accts.EnumerateArray())
                {
                    var kind = JsonUi.Str(a, "kind").ToLowerInvariant();
                    var ifpp = a.TryGetProperty("is_cash_for_ifpp", out var f) && f.ValueKind == JsonValueKind.True;
                    if (ifpp || kind is "checking" or "savings" or "cash")
                    {
                        _hasCash = true;
                        break;
                    }
                }
            }
            if (!_hasCash)
            {
                VerdictText.Text = "No cash accounts yet";
                ReasonText.Text =
                    "Add a checking or savings account in Get started or Accounts, " +
                    "then come back — Can I buy needs Safe to spend.";
                SetBusy(false);
            }
        }
        catch
        {
            /* optional */
        }
    }

    private void SetBusy(bool busy)
    {
        _busy = busy;
        CheckBtn.IsEnabled = !busy && _hasCash;
        SimBtn.IsEnabled = !busy && _hasCash;
        ScenarioBtn.IsEnabled = !busy && _hasCash;
        AmountBox.IsEnabled = !busy;
        CategoryBox.IsEnabled = !busy;
        PreferBox.IsEnabled = !busy;
        if (busy)
            ScenarioMsg.Text = "Checking purchase…";
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
        if (_busy) return;
        ErrorBar.IsOpen = false;
        BudgetCheckText.Text = "";
        CutPanel.Children.Clear();

        var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
        if (amount <= 0)
        {
            ErrorBar.Message = "Enter an amount greater than zero.";
            ErrorBar.IsOpen = true;
            return;
        }
        if (!_hasCash)
        {
            await RefreshBooksHintAsync();
            if (!_hasCash)
            {
                ErrorBar.Message = "Add a cash account before checking purchases.";
                ErrorBar.IsOpen = true;
                return;
            }
        }

        SetBusy(true);
        try
        {
            if (App.Backend is not null)
                await App.Backend.EnsureRunningAsync();

            var prefer = "auto";
            if (PreferBox.SelectedItem is ComboBoxItem item && item.Tag is string t)
                prefer = t;

            int? catId = null;
            if (CategoryBox.SelectedIndex > 0 && CategoryBox.SelectedIndex < _categories.Count)
            {
                var id = _categories[CategoryBox.SelectedIndex].Id;
                if (id > 0) catId = id;
            }

            using var api = new LedgerApiClient();
            var res = await api.PrePurchaseAsync(
                amount, prefer, categoryId: catId, allowEnvelopeRaid: _raidMode);
            var verdict = res.GetProperty("verdict").GetString() ?? "";
            VerdictText.Text = verdict switch
            {
                "safe" => "Yes — safe",
                "safe_via_other_method" => "Yes — use the other method",
                "safe_budget_tight" => "Maybe — cash ok, budget tight",
                "safe_raid_envelope" => "Yes — if you raid envelopes",
                _ => "No — don't buy yet",
            };

            var rec = res.GetProperty("recommended");
            RecText.Text =
                $"Use: {UiCopy.PayMethod(JsonUi.Str(rec, "method"))} · {JsonUi.Str(rec, "account_name")}";
            ReasonText.Text = JsonUi.Str(rec, "reason");
            if (rec.TryGetProperty("remaining_after", out var rem) && rem.ValueKind != JsonValueKind.Null)
                ReasonText.Text += $"\nSafe to spend after: {JsonUi.Money(rec, "remaining_after")}";

            // Snapshot: raw cash vs reserve (matches Home)
            if (res.TryGetProperty("ifpp_snapshot", out var snap) && snap.ValueKind == JsonValueKind.Object)
            {
                var reserve = JsonUi.Str(snap, "budget_reserve", "0");
                var sts = JsonUi.Str(snap, "safe_to_spend");
                if (!string.IsNullOrEmpty(sts) && sts != "—")
                    ReasonText.Text += $"\nSafe to spend now: {JsonUi.Money(snap, "safe_to_spend")}" +
                        (reserve is not ("0" or "0.00" or "—")
                            ? $" (after ${reserve} budget reserve)"
                            : "");
            }

            // Envelope raid offer (liquidity ok without budgets, Safe short)
            if (res.TryGetProperty("envelope_raid", out var er) && er.ValueKind == JsonValueKind.Object
                && er.TryGetProperty("available", out var av) && av.ValueKind == JsonValueKind.True
                && !_raidMode)
            {
                RaidPanel.Visibility = Visibility.Visible;
                RaidText.Text = JsonUi.Str(er, "message");
            }
            else
            {
                RaidPanel.Visibility = Visibility.Collapsed;
                RaidText.Text = "";
            }

            // Category budget from API (server-side)
            ApplyBudgetCheckFromResponse(res);

            // One-glance: recommended is above; options = other *safe* paths first, then unsafe collapsed summary
            var safeOpts = new List<string>();
            var unsafeN = 0;
            if (res.TryGetProperty("options", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var o in arr.EnumerateArray())
                {
                    var safe = o.TryGetProperty("safe", out var sf) && sf.GetBoolean();
                    var line =
                        $"{(safe ? "✓" : "✗")} {UiCopy.PayMethod(JsonUi.Str(o, "method"))} · {JsonUi.Str(o, "account_name")} — " +
                        JsonUi.Str(o, "reason");
                    if (safe) safeOpts.Add(line);
                    else unsafeN++;
                }
            }
            if (unsafeN > 0)
                safeOpts.Add($"… {unsafeN} other path{(unsafeN == 1 ? "" : "s")} not safe for this amount");
            OptionsList.ItemsSource = safeOpts.Count > 0 ? safeOpts : new List<string> { "No alternate safe options." };
            ScopeText.Text =
                $"{UiCopy.MoneyView(AppState.IfppScope)}" +
                $" · as of {JsonUi.Str(res, "as_of")}" +
                (_raidMode ? " · envelope raid" : "");

            // Show cut offers when purchase is tight or category short
            var tight = verdict is not ("safe" or "safe_via_other_method" or "safe_raid_envelope");
            await LoadCutOffersAsync(api, force: tight || BudgetCheckText.Text.Contains("short", StringComparison.OrdinalIgnoreCase));
            ScenarioMsg.Text = _raidMode ? "Checked with envelope raid allowed." : "";
            _raidMode = false; // one-shot unless user taps raid again
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            VerdictText.Text = "Couldn't check";
            ReasonText.Text = ex.Message;
            _raidMode = false;
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void Raid_Click(object sender, RoutedEventArgs e)
    {
        _raidMode = true;
        Check_Click(sender, e);
    }

    private void ApplyBudgetCheckFromResponse(JsonElement res)
    {
        if (!res.TryGetProperty("budget_check", out var bc) || bc.ValueKind != JsonValueKind.Object)
        {
            BudgetCheckText.Text = "";
            return;
        }
        BudgetCheckText.Text = JsonUi.Str(bc, "message");
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
                        btn.IsEnabled = false;
                        await api.ApplyBudgetCutAsync(ruleId, kind, dict, "Applied from Can I buy?");
                        ScenarioMsg.Text = "Cut applied — re-checking purchase…";
                        // Auto re-check Safe to spend + budget remaining
                        Check_Click(btn, new RoutedEventArgs());
                        ScenarioMsg.Text = "Cut applied and purchase re-checked.";
                    }
                    catch (Exception ex)
                    {
                        ErrorBar.Message = ex.Message;
                        ErrorBar.IsOpen = true;
                    }
                    finally
                    {
                        btn.IsEnabled = true;
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
        if (_busy) return;
        ErrorBar.IsOpen = false;
        SetBusy(true);
        try
        {
            var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
            if (amount <= 0)
                throw new InvalidOperationException("Enter an amount first.");
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
            ScenarioMsg.Text = "";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void SaveScenario_Click(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        ErrorBar.IsOpen = false;
        SetBusy(true);
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
        finally
        {
            SetBusy(false);
        }
    }
}
