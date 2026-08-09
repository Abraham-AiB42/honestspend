using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class SetupPage : Page
{
    public SetupPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        // Prefer guided first-run when nothing is set up yet
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var s = await api.GetOnboardingAsync();
            var needs = s.TryGetProperty("needs_setup", out var n) && n.GetBoolean();
            if (needs)
            {
                Frame?.Navigate(typeof(FirstRunPage));
                return;
            }
        }
        catch { /* fall through to status form */ }
        await LoadStatusAsync();
    }

    private void CardToggle(object sender, RoutedEventArgs e)
        => CardPanel.Visibility = AddCardBox.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;

    private async Task LoadStatusAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var s = await api.GetOnboardingAsync();
            var complete = s.TryGetProperty("complete", out var c) && c.GetBoolean();
            var needs = s.TryGetProperty("needs_setup", out var n) && n.GetBoolean();
            StatusSummary.Text = complete
                ? "Onboarding marked complete. You can still run quick setup to add starter accounts."
                : needs
                    ? "No accounts yet — run quick setup to get Spendable in under a minute."
                    : "Partial setup — finish accounts/bills when ready.";
            Checklist.ItemsSource = new[]
            {
                Check("Cash account", s, "has_cash_account"),
                Check("Credit account", s, "has_credit_account"),
                Check("Recurring bills", s, "has_recurring"),
                $"Accounts: {JsonUi.Str(s, "account_count", "0")}",
                $"Product: {JsonUi.Str(s, "product_name", "HonestSpend")}",
            };

            var profiles = await api.GetProfilesAsync();
            ProfileSlugBox.Items.Clear();
            var idx = 0;
            var i = 0;
            foreach (var p in profiles.EnumerateArray())
            {
                var slug = JsonUi.Str(p, "slug");
                ProfileSlugBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(p, "display_name")} ({JsonUi.Str(p, "entity_type")})",
                    Tag = slug,
                });
                if (slug == "personal") idx = i;
                i++;
            }
            if (ProfileSlugBox.Items.Count > 0)
                ProfileSlugBox.SelectedIndex = idx;
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static string Check(string label, JsonElement s, string prop)
    {
        var ok = s.TryGetProperty(prop, out var p) && p.ValueKind == JsonValueKind.True;
        return $"{(ok ? "✓" : "○")} {label}";
    }

    private async void Setup_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var slug = "personal";
            if (ProfileSlugBox.SelectedItem is ComboBoxItem pi && pi.Tag is string s)
                slug = s;
            var mode = "conservative";
            if (ModeBox.SelectedItem is ComboBoxItem mi && mi.Tag is string m)
                mode = m;

            var body = new Dictionary<string, object?>
            {
                ["profile_slug"] = slug,
                ["cash_name"] = string.IsNullOrWhiteSpace(CashNameBox.Text) ? "Primary checking" : CashNameBox.Text.Trim(),
                ["cash_balance"] = double.IsNaN(CashBalBox.Value) ? 0m : (decimal)CashBalBox.Value,
                ["cash_institution"] = string.IsNullOrWhiteSpace(InstBox.Text) ? null : InstBox.Text.Trim(),
                ["safety_buffer"] = double.IsNaN(BufferBox.Value) ? 1000m : (decimal)BufferBox.Value,
                ["ifpp_mode"] = mode,
            };

            if (AddCardBox.IsChecked == true)
            {
                if (string.IsNullOrWhiteSpace(CardNameBox.Text))
                    throw new InvalidOperationException("Card nickname required when adding a card.");
                body["card_name"] = CardNameBox.Text.Trim();
                body["card_balance"] = double.IsNaN(CardBalBox.Value) ? 0m : (decimal)CardBalBox.Value;
                if (!double.IsNaN(CardLimitBox.Value) && CardLimitBox.Value > 0)
                    body["card_limit"] = (decimal)CardLimitBox.Value;
                if (!double.IsNaN(CardDueBox.Value))
                    body["card_due_day"] = (int)CardDueBox.Value;
                if (!double.IsNaN(PromoAprBox.Value))
                    body["card_promo_apr"] = (decimal)PromoAprBox.Value;
                if (PromoEndBox.Date is not null)
                    body["card_promo_end"] = PromoEndBox.Date.Value.Date.ToString("yyyy-MM-dd");
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.QuickSetupAsync(body);
            MsgText.Text =
                $"Done. Cash: {JsonUi.Str(res, "cash_account")} · " +
                (res.TryGetProperty("card_account", out _)
                    ? $"Card: {JsonUi.Str(res, "card_account")} · "
                    : "") +
                "Open Spendable.";
            await LoadStatusAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Skip_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CompleteOnboardingAsync();
            MsgText.Text = "Marked complete without creating accounts.";
            await LoadStatusAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void Home_Click(object sender, RoutedEventArgs e)
    {
        Frame?.Navigate(typeof(HomePage));
    }

    private void FirstRun_Click(object sender, RoutedEventArgs e)
    {
        Frame?.Navigate(typeof(FirstRunPage));
    }
}
