using System.Diagnostics;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using Windows.UI;

namespace HonestSpend_WinUI.Pages;

public sealed partial class LicensePage : Page
{
    public LicensePage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync(syncStore: true);
    }

    private async Task LoadAsync(bool syncStore)
    {
        MsgText.Text = "";
        StoreMsg.Text = "";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            if (syncStore && PackageInfo.IsPackaged)
            {
                StoreMsg.Text = "Checking Microsoft Store…";
                var synced = await StoreLicenseService.SyncToEngineAsync();
                if (synced is { } el)
                {
                    ApplyStatus(el);
                    StoreMsg.Text = JsonUi.Str(el, "message");
                    if (string.IsNullOrEmpty(StoreMsg.Text))
                        StoreMsg.Text = "Store check complete.";
                    return;
                }
            }

            var st = await api.GetLicenseAsync();
            ApplyStatus(st);
        }
        catch (Exception ex)
        {
            StatusTitle.Text = "Engine offline";
            StatusDetail.Text = "Start the engine from Settings, then refresh.";
            PriceLine.Text = "";
            PromoLine.Text = "";
            TechText.Text = ex.Message;
        }
    }

    private void ApplyStatus(JsonElement st)
    {
        var licensed = st.TryGetProperty("licensed", out var lic) && lic.ValueKind == JsonValueKind.True;
        var enforce = st.TryGetProperty("enforce", out var en) && en.ValueKind == JsonValueKind.True;
        var mode = JsonUi.Str(st, "mode");
        var gate = JsonUi.Str(st, "gate");
        var dist = JsonUi.Str(st, "distribution");
        if (string.IsNullOrEmpty(dist) || dist == "—")
            dist = PackageInfo.Distribution;
        var price = st.TryGetProperty("price_usd", out var p) && p.TryGetDouble(out var pd)
            ? pd.ToString("0.00")
            : "49.99";

        if (!enforce)
        {
            StatusTitle.Text = "Unlocked (dev / unpackaged)";
            StatusDetail.Text =
                "This install does not require a purchase to use the app. " +
                "Microsoft Store packages enforce licensing after install. " +
                JsonUi.Str(st, "buy_hint");
            StatusTitle.Foreground = new SolidColorBrush(Color.FromArgb(255, 80, 200, 120));
        }
        else if (licensed)
        {
            var trial = st.TryGetProperty("is_trial", out var tr) && tr.ValueKind == JsonValueKind.True;
            StatusTitle.Text = trial ? "Licensed (trial)" : "Licensed";
            var plan = JsonUi.Str(st, "plan");
            var source = JsonUi.Str(st, "source");
            StatusDetail.Text =
                $"Plan: {plan}. Source: {source}. " +
                "You’re set on this device.";
            StatusTitle.Foreground = new SolidColorBrush(Color.FromArgb(255, 80, 200, 120));
        }
        else
        {
            StatusTitle.Text = "Purchase required";
            StatusDetail.Text =
                JsonUi.Str(st, "activate_hint") + " " + JsonUi.Str(st, "buy_hint");
            StatusTitle.Foreground = new SolidColorBrush(Color.FromArgb(255, 240, 180, 41));
        }

        PriceLine.Text =
            $"List ${price} USD one-time · mode={mode} · gate={gate} · build={dist}";
        var promo = JsonUi.Str(st, "promo_hint");
        PromoLine.Text = string.IsNullOrEmpty(promo) || promo == "—" ? "" : promo;
        PromoLine.Visibility = string.IsNullOrEmpty(PromoLine.Text)
            ? Visibility.Collapsed
            : Visibility.Visible;

        var email = JsonUi.Str(st, "email");
        if (!string.IsNullOrEmpty(email) && email != "—" && string.IsNullOrWhiteSpace(EmailBox.Text))
            EmailBox.Text = email;

        TechText.Text =
            $"device: {JsonUi.Str(st, "device_id")}\n" +
            $"license_id: {JsonUi.Str(st, "license_id")}\n" +
            $"last_verified: {JsonUi.Str(st, "last_verified_at")}\n" +
            $"packaged={PackageInfo.IsPackaged} · enforce_client={PackageInfo.ShouldEnforceLicense}\n" +
            $"grace_days: {JsonUi.Str(st, "grace_days")}";
    }

    private async void RestoreStore_Click(object sender, RoutedEventArgs e)
    {
        StoreMsg.Text = "Checking Microsoft Store…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var check = await StoreLicenseService.CheckAppLicenseAsync();
            var st = await api.RegisterStoreLicenseAsync(
                isActive: check.IsActive,
                isTrial: check.IsTrial,
                detail: check.Detail);
            ApplyStatus(st);
            StoreMsg.Text = JsonUi.Str(st, "message");
            if (string.IsNullOrEmpty(StoreMsg.Text) || StoreMsg.Text == "—")
                StoreMsg.Text = check.Detail;
        }
        catch (Exception ex)
        {
            StoreMsg.Text = ex.Message;
        }
    }

    private void OpenStore_Click(object sender, RoutedEventArgs e)
        => StoreLicenseService.OpenStoreListing();

    private async void Activate_Click(object sender, RoutedEventArgs e)
    {
        MsgText.Text = "Activating…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var key = KeyBox.Text?.Trim() ?? "";
            var email = string.IsNullOrWhiteSpace(EmailBox.Text) ? null : EmailBox.Text.Trim();
            var st = await api.ActivateLicenseAsync(key, email);
            ApplyStatus(st);
            MsgText.Text = JsonUi.Str(st, "message");
            if (string.IsNullOrEmpty(MsgText.Text))
                MsgText.Text = "Activated.";
        }
        catch (Exception ex)
        {
            MsgText.Text = ex.Message;
        }
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        MsgText.Text = "Refreshing…";
        await LoadAsync(syncStore: PackageInfo.IsPackaged);
        if (string.IsNullOrEmpty(MsgText.Text))
            MsgText.Text = "Status updated.";
    }

    private async void Clear_Click(object sender, RoutedEventArgs e)
    {
        MsgText.Text = "Clearing…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.ClearLicenseAsync();
            ApplyStatus(st);
            MsgText.Text = "Local license record cleared (Store purchase unchanged).";
        }
        catch (Exception ex)
        {
            MsgText.Text = ex.Message;
        }
    }

    private void OpenSite_Click(object sender, RoutedEventArgs e)
        => OpenUrl("https://honestspend.net/");

    private void OpenPrivacy_Click(object sender, RoutedEventArgs e)
        => OpenUrl("https://honestspend.net/privacy/");

    private static void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch
        {
            /* ignore */
        }
    }
}
