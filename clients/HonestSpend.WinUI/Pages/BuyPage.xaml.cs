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
    private bool _hasAccounts;
    private bool _raidMode;
    private bool _suppressPromoNav;

    private decimal _lastAmount;
    private int _proposedAccountId;
    private int _recommendedAccountId;
    private string _rewardCategory = "general";
    private object? _promo;
    private int? _budgetCategoryId;
    private bool _checkReady;
    private string _commitKeyRec = "";
    private string _commitKeyOrig = "";

    public BuyPage()
    {
        _suppressPromoNav = true;
        InitializeComponent();
        _suppressPromoNav = false;
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        SetIdleResult();
        await LoadCategoriesAsync();
        await LoadAccountsAsync();
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
        HideCommitActions();
    }

    private void HideCommitActions()
    {
        _checkReady = false;
        CommitPanel.Visibility = Visibility.Collapsed;
        CommitOrigBtn.Visibility = Visibility.Collapsed;
    }

    private async Task LoadAccountsAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var accts = await api.GetAccountsAsync(AppState.SelectedProfileId);
            ProposedAccountBox.Items.Clear();
            _hasCash = false;
            _hasAccounts = false;
            var defaultIndex = -1;
            var pickedCash = false;
            var pickedIfppChecking = false;
            if (accts.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in accts.EnumerateArray())
                {
                    var kind = JsonUi.Str(a, "kind").ToLowerInvariant();
                    var ifpp = a.TryGetProperty("is_cash_for_ifpp", out var f) && f.ValueKind == JsonValueKind.True;
                    var isCash = ifpp || kind is "checking" or "savings" or "cash";
                    var isCard = kind == "credit";
                    if (!isCash && !isCard)
                        continue;
                    var id = JsonUi.Int(a, "id", 0);
                    if (id <= 0)
                        continue;
                    if (isCash) _hasCash = true;
                    _hasAccounts = true;
                    ProposedAccountBox.Items.Add(new ComboBoxItem
                    {
                        Content = $"{JsonUi.Str(a, "nickname")} · {UiCopy.AccountKind(kind)}",
                        Tag = id,
                    });
                    var idx = ProposedAccountBox.Items.Count - 1;
                    if (isCash && ifpp && kind == "checking")
                    {
                        defaultIndex = idx;
                        pickedIfppChecking = true;
                        pickedCash = true;
                    }
                    else if (!pickedIfppChecking && isCash && !pickedCash)
                    {
                        defaultIndex = idx;
                        pickedCash = true;
                    }
                    else if (defaultIndex < 0)
                    {
                        defaultIndex = idx;
                    }
                }
            }
            if (ProposedAccountBox.Items.Count > 0)
                ProposedAccountBox.SelectedIndex = defaultIndex >= 0 ? defaultIndex : 0;
            if (_hasAccounts && !_hasCash)
                ScopeText.Text = "No cash account yet — cards can still be checked.";
            if (!_hasAccounts)
            {
                VerdictText.Text = "No accounts yet";
                ReasonText.Text =
                    "Add a checking or savings account in Add (or Get started), " +
                    "then come back — Can I buy needs an account to charge.";
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
        var ready = !busy && _hasAccounts;
        CheckBtn.IsEnabled = ready;
        SimBtn.IsEnabled = ready;
        ScenarioBtn.IsEnabled = ready;
        CommitRecBtn.IsEnabled = ready;
        CommitOrigBtn.IsEnabled = ready;
        AmountBox.IsEnabled = !busy;
        CategoryBox.IsEnabled = !busy;
        ProposedAccountBox.IsEnabled = !busy;
        RewardCategoryBox.IsEnabled = !busy;
        PromoBox.IsEnabled = !busy;
        PlanMonthsBox.IsEnabled = !busy;
        PlanMonthlyBox.IsEnabled = !busy;
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

    private void Promo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var mode = SelectedPromoMode();
        PlanPanel.Visibility = mode == "purchase_plan" ? Visibility.Visible : Visibility.Collapsed;
        if (_suppressPromoNav || _busy) return;
        if (mode == "new_offer")
            OpenOffersDesk();
    }

    private void OpenOffersDesk()
    {
        NoticeBar.Message = "Opens Offers in Full books";
        NoticeBar.IsOpen = true;
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("offers");
        else
            Frame?.Navigate(typeof(CreditPage), "offers");
    }

    private string SelectedPromoMode()
    {
        if (PromoBox.SelectedItem is ComboBoxItem item && item.Tag is string t)
            return t;
        return "none";
    }

    private string SelectedRewardCategory()
    {
        if (RewardCategoryBox.SelectedItem is ComboBoxItem item && item.Tag is string t
            && !string.IsNullOrWhiteSpace(t))
            return t;
        return "general";
    }

    private int? SelectedProposedAccountId()
    {
        if (ProposedAccountBox.SelectedItem is ComboBoxItem item && item.Tag is int id && id > 0)
            return id;
        return null;
    }

    private int? SelectedBudgetCategoryId()
    {
        if (CategoryBox.SelectedIndex > 0 && CategoryBox.SelectedIndex < _categories.Count)
        {
            var id = _categories[CategoryBox.SelectedIndex].Id;
            if (id > 0) return id;
        }
        return null;
    }

    private object? BuildPromo()
    {
        var mode = SelectedPromoMode();
        if (mode is "none" or "new_offer" or "")
            return null;
        if (mode == "card_intro")
            return new { mode = "card_intro" };

        int? months = null;
        if (!double.IsNaN(PlanMonthsBox.Value) && PlanMonthsBox.Value >= 1)
            months = (int)PlanMonthsBox.Value;
        string? monthly = null;
        if (!double.IsNaN(PlanMonthlyBox.Value) && PlanMonthlyBox.Value > 0)
            monthly = ((decimal)PlanMonthlyBox.Value).ToString("0.00", System.Globalization.CultureInfo.InvariantCulture);
        return new { mode = "purchase_plan", months, monthly };
    }

    private async void Check_Click(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        ErrorBar.IsOpen = false;
        BudgetCheckText.Text = "";
        CutPanel.Children.Clear();
        HideCommitActions();

        if (SelectedPromoMode() == "new_offer")
        {
            OpenOffersDesk();
            return;
        }

        var amount = (decimal)(AmountBox.Value is double.NaN ? 0 : AmountBox.Value);
        if (amount <= 0)
        {
            ErrorBar.Message = "Enter an amount greater than zero.";
            ErrorBar.IsOpen = true;
            return;
        }
        var proposedId = SelectedProposedAccountId();
        if (proposedId is null)
        {
            if (!_hasAccounts)
            {
                await LoadAccountsAsync();
                proposedId = SelectedProposedAccountId();
            }
            if (proposedId is null)
            {
                ErrorBar.Message = "Pick a proposed account.";
                ErrorBar.IsOpen = true;
                return;
            }
        }

        SetBusy(true);
        try
        {
            if (App.Backend is not null)
                await App.Backend.EnsureRunningAsync();

            var catId = SelectedBudgetCategoryId();
            var reward = SelectedRewardCategory();
            var promo = BuildPromo();

            using var api = new LedgerApiClient();
            var res = await api.PrePurchaseAsync(
                amount, proposedId.Value, reward, promo, catId, _raidMode);
            var verdict = res.GetProperty("verdict").GetString() ?? "";
            VerdictText.Text = verdict switch
            {
                "safe" => "Yes — safe",
                "safe_via_other_account" => "Yes — use the recommended account",
                "safe_via_other_method" => "Yes — use the recommended account",
                "safe_budget_tight" => "Maybe — cash ok, budget tight",
                "safe_raid_envelope" => "Yes — if you raid envelopes",
                _ => "No — don't buy yet",
            };

            var rec = res.GetProperty("recommended");
            var recId = JsonUi.Int(rec, "account_id", 0);
            var recName = JsonUi.Str(rec, "account_name");
            RecText.Text = FormatHeadline(rec, reward);

            var proposedEl = default(JsonElement);
            var hasProposed = res.TryGetProperty("proposed", out proposedEl)
                && proposedEl.ValueKind == JsonValueKind.Object;
            var propId = hasProposed ? JsonUi.Int(proposedEl, "account_id", proposedId.Value) : proposedId.Value;

            var why = JsonUi.Str(res, "why", "");
            var reasonBits = new List<string>();
            if (!string.IsNullOrEmpty(why) && why != "—")
                reasonBits.Add(why);
            if (recId > 0 && recId != propId)
            {
                reasonBits.Add($"Recommended ({recName}): {JsonUi.Str(rec, "reason")}");
                if (hasProposed)
                    reasonBits.Add($"Your pick ({JsonUi.Str(proposedEl, "account_name")}): {JsonUi.Str(proposedEl, "reason")}");
            }
            else if (reasonBits.Count == 0)
            {
                reasonBits.Add(JsonUi.Str(rec, "reason"));
            }
            ReasonText.Text = string.Join("\n", reasonBits);
            if (rec.TryGetProperty("remaining_after", out var rem) && rem.ValueKind != JsonValueKind.Null)
                ReasonText.Text += $"\nSafe to spend after: {JsonUi.Money(rec, "remaining_after")}";

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

            ApplyBudgetCheckFromResponse(res);

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

            var tight = verdict is not ("safe" or "safe_via_other_method" or "safe_via_other_account" or "safe_raid_envelope");
            await LoadCutOffersAsync(api, force: tight || BudgetCheckText.Text.Contains("short", StringComparison.OrdinalIgnoreCase));
            ScenarioMsg.Text = _raidMode ? "Checked with envelope raid allowed." : "";
            _raidMode = false;

            if (recId > 0)
            {
                _lastAmount = amount;
                _proposedAccountId = propId;
                _recommendedAccountId = recId;
                _rewardCategory = reward;
                _promo = promo;
                _budgetCategoryId = catId;
                _commitKeyRec = Guid.NewGuid().ToString("N");
                _commitKeyOrig = Guid.NewGuid().ToString("N");
                _checkReady = true;
                CommitRecBtn.Content = recName is "—" or ""
                    ? "I charged the recommended account"
                    : $"I charged the recommended account ({recName})";
                CommitPanel.Visibility = Visibility.Visible;
                if (propId > 0 && propId != recId)
                {
                    var origName = hasProposed ? JsonUi.Str(proposedEl, "account_name") : "";
                    CommitOrigBtn.Content = string.IsNullOrEmpty(origName) || origName == "—"
                        ? "I charged my original pick"
                        : $"I charged my original pick ({origName})";
                    CommitOrigBtn.Visibility = Visibility.Visible;
                }
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            VerdictText.Text = "Couldn't check";
            ReasonText.Text = ex.Message;
            _raidMode = false;
            HideCommitActions();
        }
        finally
        {
            SetBusy(false);
        }
    }

    private static string FormatHeadline(JsonElement rec, string rewardCategory)
    {
        var name = JsonUi.Str(rec, "account_name");
        if (string.IsNullOrEmpty(name) || name == "—")
            return "No recommended account.";
        var bits = new List<string> { $"Charge {name}" };
        var floatDays = JsonUi.Int(rec, "float_days", -1);
        if (floatDays >= 0)
            bits.Add($"{floatDays} day{(floatDays == 1 ? "" : "s")} interest-free");
        var rate = JsonUi.Str(rec, "rewards_rate", "");
        if (!string.IsNullOrEmpty(rate) && rate != "—"
            && decimal.TryParse(rate, System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture, out var r) && r > 0)
            bits.Add($"{r:0.##}% {rewardCategory}");
        return string.Join(" — ", bits);
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

    private async void CommitRec_Click(object sender, RoutedEventArgs e)
        => await CommitAsync(_recommendedAccountId, _commitKeyRec);

    private async void CommitOrig_Click(object sender, RoutedEventArgs e)
        => await CommitAsync(_proposedAccountId, _commitKeyOrig);

    private async Task CommitAsync(int postAccountId, string commitKey)
    {
        if (_busy || !_checkReady || postAccountId <= 0) return;
        ErrorBar.IsOpen = false;
        if (string.IsNullOrWhiteSpace(commitKey))
            commitKey = Guid.NewGuid().ToString("N");
        SetBusy(true);
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            JsonElement res;
            try
            {
                res = await api.CommitPurchaseAsync(CommitBody(postAccountId, confirmUnsafe: false, commitKey));
            }
            catch (Exception ex) when (IsCommitConflict(ex, out var message))
            {
                var dlg = new ContentDialog
                {
                    Title = "This charge is not safe",
                    Content = string.IsNullOrWhiteSpace(message)
                        ? "Posting this charge is not safe. Record it anyway?"
                        : message + " Record it anyway?",
                    PrimaryButtonText = "Record anyway",
                    CloseButtonText = "Cancel",
                    DefaultButton = ContentDialogButton.Close,
                    XamlRoot = XamlRoot,
                };
                if (await dlg.ShowAsync() != ContentDialogResult.Primary)
                {
                    ErrorBar.Message = string.IsNullOrWhiteSpace(message) ? "Charge not recorded." : message;
                    ErrorBar.IsOpen = true;
                    return;
                }
                res = await api.CommitPurchaseAsync(CommitBody(postAccountId, confirmUnsafe: true, commitKey));
            }

            var postedName = "";
            if (res.TryGetProperty("posted", out var posted) && posted.ValueKind == JsonValueKind.Object)
                postedName = JsonUi.Str(posted, "account_name", "");
            if (string.IsNullOrEmpty(postedName) || postedName == "—")
                postedName = JsonUi.Str(res, "account_name", "");
            ScenarioMsg.Text = string.IsNullOrEmpty(postedName) || postedName == "—"
                ? "Recorded the charge."
                : $"Recorded the charge on {postedName}.";
            HideCommitActions();
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

    private object CommitBody(int postAccountId, bool confirmUnsafe, string commitKey) => new
    {
        amount = _lastAmount,
        post_account_id = postAccountId,
        proposed_account_id = _proposedAccountId,
        recommended_account_id = _recommendedAccountId,
        reward_category = _rewardCategory,
        promo = _promo,
        category_id = _budgetCategoryId,
        confirm_unsafe = confirmUnsafe,
        commit_key = commitKey,
        profile_id = AppState.SelectedProfileId,
    };

    private static bool IsCommitConflict(Exception ex, out string message)
    {
        message = "";
        if (NeverNegUi.TryParseWouldGoNegative(ex, out _, out var nn))
        {
            message = NeverNegUi.FriendlyMessage(nn);
            return true;
        }
        var m = ex.Message ?? "";
        if (!m.StartsWith("409 ", StringComparison.Ordinal)
            && !m.Contains("unsafe_purchase", StringComparison.OrdinalIgnoreCase))
            return false;
        var brace = m.IndexOf('{');
        if (brace >= 0)
        {
            try
            {
                using var doc = JsonDocument.Parse(m[brace..]);
                var root = doc.RootElement;
                var detail = root;
                if (root.TryGetProperty("detail", out var d))
                    detail = d;
                if (detail.ValueKind == JsonValueKind.Object)
                {
                    if (detail.TryGetProperty("message", out var msgEl) && msgEl.ValueKind == JsonValueKind.String)
                        message = msgEl.GetString() ?? "";
                    else if (detail.TryGetProperty("reason", out var reasonEl) && reasonEl.ValueKind == JsonValueKind.String)
                        message = reasonEl.GetString() ?? "";
                }
                else if (detail.ValueKind == JsonValueKind.String)
                {
                    message = detail.GetString() ?? "";
                }
            }
            catch
            {
                /* fall through */
            }
        }
        if (string.IsNullOrWhiteSpace(message))
            message = "This charge is not safe for the posted account.";
        return true;
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
