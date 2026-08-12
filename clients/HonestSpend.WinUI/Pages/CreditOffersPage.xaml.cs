using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class CreditOffersPage : Page
{
    private readonly List<(int Id, string Name)> _creditCards = new();
    private bool _suppressEditorCard;
    private bool _suppressEditorPick;

    public CreditOffersPage()
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
            await LoadCardsAsync(api);
            await LoadPendingAsync(api);
            await LoadConflictsAsync(api);
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task LoadCardsAsync(LedgerApiClient api)
    {
        _creditCards.Clear();
        var prevFrom = SelectedInt(FromCardBox);
        var prevDest = SelectedInt(DestCardBox);
        var prevPlan = SelectedInt(PlanCardBox);
        var prevEdit = SelectedInt(EditorCardBox);

        FromCardBox.Items.Clear();
        DestCardBox.Items.Clear();
        PlanCardBox.Items.Clear();
        EditorCardBox.Items.Clear();

        DestCardBox.Items.Add(new ComboBoxItem { Content = "New card from this offer", Tag = 0 });

        var accounts = await api.GetAccountsAsync();
        if (accounts.ValueKind == JsonValueKind.Array)
        {
            foreach (var a in accounts.EnumerateArray())
            {
                if (!string.Equals(JsonUi.Str(a, "kind"), "credit", StringComparison.OrdinalIgnoreCase))
                    continue;
                var id = a.GetProperty("id").GetInt32();
                var name = JsonUi.Str(a, "nickname");
                _creditCards.Add((id, name));
                var label = $"{name} · {JsonUi.Money(a, "current_balance")}";
                FromCardBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
                DestCardBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
                PlanCardBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
                EditorCardBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
            }
        }

        SelectInt(FromCardBox, prevFrom);
        SelectInt(DestCardBox, prevDest);
        SelectInt(PlanCardBox, prevPlan);
        _suppressEditorCard = true;
        SelectInt(EditorCardBox, prevEdit);
        _suppressEditorCard = false;
    }

    private async Task LoadPendingAsync(LedgerApiClient api)
    {
        PendingOfferPanel.Children.Clear();
        var res = await api.GetOffersAsync("pending");
        var count = 0;
        if (res.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in arr.EnumerateArray())
            {
                count++;
                PendingOfferPanel.Children.Add(BuildOfferCard(item));
            }
        }
        PendingEmptyText.Visibility = count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private UIElement BuildOfferCard(JsonElement item)
    {
        var id = JsonUi.Int(item, "id");
        var name = JsonUi.Str(item, "name");
        var type = JsonUi.Str(item, "offer_type", "offer");
        var terms = item.TryGetProperty("terms", out var t) && t.ValueKind == JsonValueKind.Object
            ? t
            : item;
        var typeLabel = type switch
        {
            "new_card" => "New card",
            "balance_transfer" => "Balance transfer",
            "purchase_plan" => "Purchase plan",
            _ => type,
        };
        var bits = new List<string> { typeLabel };
        var amt = MoneyOrEmpty(terms, "amount");
        if (amt is not null) bits.Add(amt);
        var months = JsonUi.Str(terms, "months", "");
        if (months is not ("" or "—" or "0" or "null")) bits.Add(months + " mo");
        var monthly = MoneyOrEmpty(terms, "monthly");
        if (monthly is not null) bits.Add("monthly " + monthly);

        JsonElement? verdict = item.TryGetProperty("verdict", out var v) && v.ValueKind == JsonValueKind.Object
            ? v
            : null;
        var verdictCode = verdict is JsonElement ve ? JsonUi.Str(ve, "verdict", "") : "";
        var why = verdict is JsonElement vw ? JsonUi.Str(vw, "why", "") : "";
        var verdictLabel = VerdictLabel(verdictCode);

        var stack = new StackPanel { Spacing = 6 };
        stack.Children.Add(new TextBlock
        {
            Text = name,
            FontWeight = FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        stack.Children.Add(new TextBlock
        {
            Text = string.Join(" · ", bits),
            Opacity = 0.75,
            TextWrapping = TextWrapping.Wrap,
            FontSize = 12,
        });
        stack.Children.Add(new TextBlock
        {
            Text = string.IsNullOrEmpty(why) || why == "—"
                ? verdictLabel
                : $"{verdictLabel} — {why}",
            TextWrapping = TextWrapping.Wrap,
        });

        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var take = new Button { Content = "Take it" };
        take.Click += async (_, _) => await DecideAsync(id, "take");
        var plan = new Button { Content = "Take with a plan" };
        plan.Click += async (_, _) => await DecideAsync(id, "take_with_plan");
        var skip = new Button { Content = "Skip" };
        skip.Click += async (_, _) => await DecideAsync(id, "skip");
        row.Children.Add(take);
        row.Children.Add(plan);
        row.Children.Add(skip);
        stack.Children.Add(row);

        return new Border
        {
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorSecondaryBrush"],
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(12),
            Child = stack,
        };
    }

    private async Task DecideAsync(int offerId, string decision)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (decision == "skip")
            {
                var confirm = new ContentDialog
                {
                    Title = "Skip this offer?",
                    Content = "Skip means you are not taking it. You can add it again later.",
                    PrimaryButtonText = "Skip",
                    CloseButtonText = "Cancel",
                    DefaultButton = ContentDialogButton.Close,
                    XamlRoot = XamlRoot,
                };
                if (await confirm.ShowAsync() != ContentDialogResult.Primary)
                    return;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.DecideOfferAsync(offerId, decision, confirmSkip: decision == "skip");
            var nick = "";
            if (res.TryGetProperty("account", out var acct) && acct.ValueKind == JsonValueKind.Object)
                nick = JsonUi.Str(acct, "nickname", "");
            OfferMsg.Text = decision switch
            {
                "skip" => "Skipped.",
                "take_with_plan" => string.IsNullOrEmpty(nick) || nick == "—"
                    ? "Taken with a plan — pay policy set so the 0% stays intact."
                    : $"Taken with a plan on {nick}.",
                _ => string.IsNullOrEmpty(nick) || nick == "—"
                    ? "Taken."
                    : $"Taken · opened {nick}.",
            };
            ApplyVerdict(res.TryGetProperty("verdict", out var v) && v.ValueKind == JsonValueKind.Object
                ? v
                : default);
            await LoadPendingAsync(api);
            await LoadCardsAsync(api);
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void OfferType_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (NewCardPanel is null) return;
        var type = SelectedOfferType();
        NewCardPanel.Visibility = type == "new_card" ? Visibility.Visible : Visibility.Collapsed;
        BtPanel.Visibility = type == "balance_transfer" ? Visibility.Visible : Visibility.Collapsed;
        PlanOfferPanel.Visibility = type == "purchase_plan" ? Visibility.Visible : Visibility.Collapsed;
    }

    private string SelectedOfferType()
    {
        if (OfferTypeBox.SelectedItem is ComboBoxItem { Tag: string t } && !string.IsNullOrWhiteSpace(t))
            return t;
        return "new_card";
    }

    private async void CreateOffer_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var type = SelectedOfferType();
            var name = OfferNameBox.Text?.Trim();
            if (string.IsNullOrEmpty(name))
                throw new InvalidOperationException("Enter a name for this offer.");

            var body = new Dictionary<string, object?>
            {
                ["offer_type"] = type,
                ["name"] = name,
                ["nickname"] = name,
            };

            if (type == "new_card")
            {
                if (!double.IsNaN(LimitBox.Value) && LimitBox.Value > 0)
                    body["credit_limit"] = (decimal)LimitBox.Value;
                if (!double.IsNaN(AnnualFeeBox.Value) && AnnualFeeBox.Value > 0)
                    body["annual_fee"] = (decimal)AnnualFeeBox.Value;
                if (!double.IsNaN(CloseDayBox.Value))
                    body["close_day"] = (int)CloseDayBox.Value;
                if (!double.IsNaN(DueDayBox.Value))
                    body["due_day"] = (int)DueDayBox.Value;
                if (!double.IsNaN(IntroAprBox.Value))
                    body["intro_apr"] = ((decimal)IntroAprBox.Value) / 100m;
                if (!double.IsNaN(IntroMonthsBox.Value) && IntroMonthsBox.Value > 0)
                    body["months"] = (int)IntroMonthsBox.Value;
                var rewards = RewardsBox.Text?.Trim();
                if (!string.IsNullOrEmpty(rewards))
                    body["rewards_program"] = rewards;
            }
            else if (type == "balance_transfer")
            {
                if (SelectedInt(FromCardBox) is not int fromId || fromId <= 0)
                    throw new InvalidOperationException("Pick the card you are transferring off.");
                body["from_account_id"] = fromId;
                if (SelectedInt(DestCardBox) is int destId && destId > 0)
                    body["destination_account_id"] = destId;
                if (double.IsNaN(BtAmountBox.Value) || BtAmountBox.Value <= 0)
                    throw new InvalidOperationException("Enter the transfer amount.");
                body["amount"] = (decimal)BtAmountBox.Value;
                if (!double.IsNaN(BtMonthsBox.Value) && BtMonthsBox.Value > 0)
                    body["months"] = (int)BtMonthsBox.Value;
                if (!double.IsNaN(BtFeeAmtBox.Value) && BtFeeAmtBox.Value > 0)
                    body["fee"] = (decimal)BtFeeAmtBox.Value;
                else if (!double.IsNaN(BtFeePctBox.Value) && BtFeePctBox.Value > 0)
                    body["fee_pct"] = (decimal)BtFeePctBox.Value;
            }
            else
            {
                if (SelectedInt(PlanCardBox) is not int cardId || cardId <= 0)
                    throw new InvalidOperationException("Pick the card for this plan.");
                body["account_id"] = cardId;
                body["destination_account_id"] = cardId;
                if (!double.IsNaN(PlanAmountBox.Value) && PlanAmountBox.Value > 0)
                    body["amount"] = (decimal)PlanAmountBox.Value;
                if (!double.IsNaN(PlanMonthsBox.Value) && PlanMonthsBox.Value > 0)
                    body["months"] = (int)PlanMonthsBox.Value;
                if (!double.IsNaN(PlanMonthlyBox.Value) && PlanMonthlyBox.Value > 0)
                    body["monthly"] = (decimal)PlanMonthlyBox.Value;
                if (!body.ContainsKey("amount") && !body.ContainsKey("monthly"))
                    throw new InvalidOperationException("Enter an amount or a monthly payment.");
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var created = await api.CreateOfferAsync(body);
            ApplyVerdict(created.TryGetProperty("verdict", out var v) && v.ValueKind == JsonValueKind.Object
                ? v
                : default);
            OfferMsg.Text = $"Saved “{JsonUi.Str(created, "name", name)}” as pending.";
            await LoadPendingAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void ApplyVerdict(JsonElement verdict)
    {
        if (verdict.ValueKind != JsonValueKind.Object)
        {
            VerdictTitle.Text = "Verdict: —";
            VerdictWhy.Text = "Save an offer to see Take / Take with a plan / Skip and why.";
            return;
        }
        var code = JsonUi.Str(verdict, "verdict", "");
        VerdictTitle.Text = "Verdict: " + VerdictLabel(code);
        var why = JsonUi.Str(verdict, "why", "");
        VerdictWhy.Text = string.IsNullOrEmpty(why) || why == "—"
            ? "Always available: Take it · Take with a plan · Skip."
            : why;
    }

    private async Task LoadConflictsAsync(LedgerApiClient api)
    {
        ConflictPanel.Children.Clear();
        var res = await api.GetPromoConflictsAsync();
        var count = 0;
        if (res.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in arr.EnumerateArray())
            {
                count++;
                ConflictPanel.Children.Add(BuildConflictCard(item));
            }
        }
        ConflictCard.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private UIElement BuildConflictCard(JsonElement item)
    {
        var id = JsonUi.Int(item, "id");
        var incoming = item.TryGetProperty("incoming_values", out var iv) && iv.ValueKind == JsonValueKind.Object
            ? iv
            : default;
        var mine = item.TryGetProperty("user_values", out var uv) && uv.ValueKind == JsonValueKind.Object
            ? uv
            : default;
        var src = JsonUi.Str(item, "incoming_source", "statement");
        var takeLabel = string.Equals(src, "plaid", StringComparison.OrdinalIgnoreCase)
            ? "Take Plaid"
            : "Take statement";
        var diffs = new List<string>();
        if (item.TryGetProperty("field_diffs", out var d) && d.ValueKind == JsonValueKind.Array)
        {
            foreach (var f in d.EnumerateArray())
            {
                var s = f.ValueKind == JsonValueKind.String ? f.GetString() : f.GetRawText();
                if (!string.IsNullOrWhiteSpace(s))
                    diffs.Add(s!);
            }
        }

        var stack = new StackPanel { Spacing = 6 };
        stack.Children.Add(new TextBlock
        {
            Text = "This statement doesn’t match the promo you entered",
            FontWeight = FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        stack.Children.Add(new TextBlock
        {
            Text = "Yours: " + FormatSnap(mine) + "\nIncoming: " + FormatSnap(incoming)
                   + (diffs.Count > 0 ? "\nDiffers: " + string.Join(", ", diffs) : ""),
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var keep = new Button { Content = "Keep mine" };
        keep.Click += async (_, _) => await ResolveConflictAsync(id, "keep_user", null);
        var take = new Button { Content = takeLabel };
        take.Click += async (_, _) => await ResolveConflictAsync(id, "take_incoming", null);
        var edit = new Button { Content = "Edit" };
        edit.Click += async (_, _) => await EditConflictAsync(id, mine, incoming);
        row.Children.Add(keep);
        row.Children.Add(take);
        row.Children.Add(edit);
        stack.Children.Add(row);

        return new Border
        {
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorSecondaryBrush"],
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(12),
            Child = stack,
        };
    }

    private async Task EditConflictAsync(int conflictId, JsonElement mine, JsonElement incoming)
    {
        var seed = incoming.ValueKind == JsonValueKind.Object ? incoming : mine;
        var nameBox = new TextBox
        {
            Header = "Name",
            Text = JsonUi.Str(seed, "name", JsonUi.Str(mine, "name", "")),
        };
        var remBox = new NumberBox
        {
            Header = "Remaining ($)",
            Minimum = 0,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact,
            Value = ParseD(seed, "principal_remaining", ParseD(mine, "principal_remaining", 0)),
        };
        var monBox = new NumberBox
        {
            Header = "Monthly ($)",
            Minimum = 0,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact,
            Value = ParseD(seed, "monthly_payment", ParseD(mine, "monthly_payment", 0)),
        };
        var endBox = new TextBox
        {
            Header = "End date",
            PlaceholderText = "yyyy-MM-dd",
            Text = JsonUi.Str(seed, "end_date", JsonUi.Str(mine, "end_date", "")),
        };
        var panel = new StackPanel { Spacing = 8, MinWidth = 280 };
        panel.Children.Add(nameBox);
        panel.Children.Add(remBox);
        panel.Children.Add(monBox);
        panel.Children.Add(endBox);

        var dlg = new ContentDialog
        {
            Title = "Edit promo (saved as yours)",
            Content = panel,
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary)
            return;

        var edits = new Dictionary<string, object?>
        {
            ["principal_remaining"] = double.IsNaN(remBox.Value) ? 0m : (decimal)remBox.Value,
            ["monthly_payment"] = double.IsNaN(monBox.Value) ? 0m : (decimal)monBox.Value,
        };
        var n = nameBox.Text?.Trim();
        if (!string.IsNullOrEmpty(n))
            edits["name"] = n;
        var end = endBox.Text?.Trim();
        if (!string.IsNullOrEmpty(end) && end is not ("—" or "?"))
            edits["end_date"] = end;

        await ResolveConflictAsync(conflictId, "edit", edits);
    }

    private async Task ResolveConflictAsync(int conflictId, string action, Dictionary<string, object?>? edits)
    {
        ErrorBar.IsOpen = false;
        try
        {
            object body = edits is null
                ? new { action }
                : new Dictionary<string, object?>(edits) { ["action"] = action };
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.ResolvePromoConflictAsync(conflictId, body);
            await LoadConflictsAsync(api);
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void EditorCard_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressEditorCard) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            EditorMsg.Text = ex.Message;
        }
    }

    private async Task LoadEditorLinesAsync(LedgerApiClient api)
    {
        if (SelectedInt(EditorCardBox) is not int cardId || cardId <= 0)
        {
            EditorLineList.ItemsSource = new List<string> { "Pick a card to see promo lines." };
            _suppressEditorPick = true;
            EditorPickBox.Items.Clear();
            _suppressEditorPick = false;
            EditorSourceText.Text = "Source: —";
            return;
        }

        var res = await api.GetPromoLinesAsync(cardId);
        var lines = new List<string>();
        _suppressEditorPick = true;
        try
        {
            EditorPickBox.Items.Clear();
            EditorPickBox.Items.Add(new ComboBoxItem
            {
                Content = "(new plan — not editing)",
                Tag = (EditorPick?)null,
            });
            if (res.TryGetProperty("items", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var ln in arr.EnumerateArray())
                {
                    if (string.Equals(JsonUi.Str(ln, "kind"), "offer", StringComparison.OrdinalIgnoreCase))
                        continue;
                    var name = JsonUi.Str(ln, "name");
                    var source = JsonUi.Str(ln, "source", "user");
                    var kind = JsonUi.Str(ln, "kind", "purchase_plan");
                    var status = JsonUi.Str(ln, "status", JsonUi.Str(ln, "active") == "false" ? "closed" : "open");
                    var end = JsonUi.Str(ln, "end_date", "");
                    lines.Add(
                        $"{name} · {kind} · remaining {JsonUi.Money(ln, "principal_remaining")} · " +
                        $"monthly {JsonUi.Money(ln, "monthly_payment")} · {status} · source {source}" +
                        (string.IsNullOrEmpty(end) || end is "—" or "null"
                            ? ""
                            : $" · ends {PlainDateUi.FormatPlainWeekdayDate(end)}"));
                    var lineId = JsonUi.Int(ln, "id");
                    if (lineId > 0)
                    {
                        EditorPickBox.Items.Add(new ComboBoxItem
                        {
                            Content = $"{name} · {source}",
                            Tag = new EditorPick(
                                lineId,
                                name,
                                ParseD(ln, "principal_remaining", 0),
                                ParseD(ln, "monthly_payment", 0),
                                end is "—" or "null" ? "" : end,
                                source),
                        });
                    }
                }
            }
            EditorPickBox.SelectedIndex = 0;
        }
        finally
        {
            _suppressEditorPick = false;
        }
        EditorLineList.ItemsSource = lines.Count > 0
            ? lines
            : new List<string> { "No promo/installment lines on this card." };
        EditorSourceText.Text = "Source: —";
    }

    private void EditorPick_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressEditorPick) return;
        if (EditorPickBox.SelectedItem is not ComboBoxItem { Tag: EditorPick pick })
        {
            EditorSourceText.Text = "Source: — (new line will be yours)";
            return;
        }
        EditorNameBox.Text = pick.Name;
        EditorRemainingBox.Value = pick.Remaining;
        EditorMonthlyBox.Value = pick.Monthly;
        EditorEndBox.Text = pick.EndDate;
        EditorSourceText.Text = "Source: " + pick.Source + " · saving overwrites as yours";
    }

    private async void EditorAdd_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedInt(EditorCardBox) is not int cardId || cardId <= 0)
                throw new InvalidOperationException("Pick a card first.");
            var name = EditorNameBox.Text?.Trim();
            if (string.IsNullOrEmpty(name))
                throw new InvalidOperationException("Enter a plan name.");
            var remaining = double.IsNaN(EditorRemainingBox.Value) ? 0m : (decimal)EditorRemainingBox.Value;
            var monthly = double.IsNaN(EditorMonthlyBox.Value) ? 0m : (decimal)EditorMonthlyBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var body = new Dictionary<string, object?>
            {
                ["name"] = name,
                ["principal_remaining"] = remaining,
                ["monthly_payment"] = monthly,
                ["start_date"] = DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                ["source"] = "user",
                ["active"] = true,
            };
            var end = EditorEndBox.Text?.Trim();
            if (!string.IsNullOrEmpty(end) && end is not ("—" or "?"))
                body["end_date"] = end;
            await api.CreatePromoLineAsync(cardId, body);
            EditorMsg.Text = $"Added “{name}” · source user";
            EditorNameBox.Text = "";
            EditorRemainingBox.Value = 0;
            EditorMonthlyBox.Value = 0;
            EditorEndBox.Text = "";
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void EditorSave_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedInt(EditorCardBox) is not int cardId || cardId <= 0)
                throw new InvalidOperationException("Pick a card first.");
            if (EditorPickBox.SelectedItem is not ComboBoxItem { Tag: EditorPick pick })
                throw new InvalidOperationException("Pick a plan to edit (or use Add for a new plan).");
            var remaining = double.IsNaN(EditorRemainingBox.Value) ? 0m : (decimal)EditorRemainingBox.Value;
            var monthly = double.IsNaN(EditorMonthlyBox.Value) ? 0m : (decimal)EditorMonthlyBox.Value;
            var body = new Dictionary<string, object?>
            {
                ["principal_remaining"] = remaining,
                ["monthly_payment"] = monthly,
                ["source"] = "user",
            };
            var name = EditorNameBox.Text?.Trim();
            if (!string.IsNullOrEmpty(name))
                body["name"] = name;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PatchPromoLineAsync(cardId, pick.Id, body);
            EditorMsg.Text =
                $"Updated “{JsonUi.Str(res, "name")}” · remaining {JsonUi.Money(res, "principal_remaining")} · " +
                $"source {JsonUi.Str(res, "source", "user")}";
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void EditorEnd_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (SelectedInt(EditorCardBox) is not int cardId || cardId <= 0)
                throw new InvalidOperationException("Pick a card first.");
            if (EditorPickBox.SelectedItem is not ComboBoxItem { Tag: EditorPick pick })
                throw new InvalidOperationException("Pick a plan to end.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PatchPromoLineAsync(cardId, pick.Id, new
            {
                active = false,
                principal_remaining = 0m,
                source = "user",
            });
            EditorMsg.Text = $"Ended “{JsonUi.Str(res, "name", pick.Name)}”.";
            await LoadEditorLinesAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static int? SelectedInt(ComboBox box)
    {
        if (box.SelectedItem is ComboBoxItem { Tag: int id })
            return id;
        return null;
    }

    private static void SelectInt(ComboBox box, int? id)
    {
        if (box.Items.Count == 0) return;
        if (id is int want)
        {
            for (var i = 0; i < box.Items.Count; i++)
            {
                if (box.Items[i] is ComboBoxItem { Tag: int t } && t == want)
                {
                    box.SelectedIndex = i;
                    return;
                }
            }
        }
        box.SelectedIndex = 0;
    }

    private static string VerdictLabel(string code) => code switch
    {
        "take" => "Take it",
        "take_with_plan" => "Take it with a plan",
        "skip" => "Skip",
        _ => string.IsNullOrEmpty(code) ? "—" : code,
    };

    private static string? MoneyOrEmpty(JsonElement el, string prop)
    {
        if (el.ValueKind != JsonValueKind.Object) return null;
        var s = JsonUi.Str(el, prop, "");
        if (string.IsNullOrEmpty(s) || s is "—" or "0" or "0.00" or "0.0000" or "null")
            return null;
        return JsonUi.Money(el, prop);
    }

    private static string FormatSnap(JsonElement snap)
    {
        if (snap.ValueKind != JsonValueKind.Object)
            return "—";
        var end = JsonUi.Str(snap, "end_date", "");
        return
            $"remaining {JsonUi.Money(snap, "principal_remaining")} · " +
            $"monthly {JsonUi.Money(snap, "monthly_payment")}" +
            (string.IsNullOrEmpty(end) || end is "—" or "null" ? "" : " · end " + end);
    }

    private static double ParseD(JsonElement s, string name, double fallback)
    {
        if (s.ValueKind != JsonValueKind.Object) return fallback;
        if (!s.TryGetProperty(name, out var el) || el.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            return fallback;
        var raw = el.ValueKind == JsonValueKind.String ? el.GetString() : el.GetRawText();
        return double.TryParse(raw, NumberStyles.Any, CultureInfo.InvariantCulture, out var d) ? d : fallback;
    }

    private sealed record EditorPick(int Id, string Name, double Remaining, double Monthly, string EndDate, string Source);
}
