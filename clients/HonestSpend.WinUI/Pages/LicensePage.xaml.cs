using System.Diagnostics;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

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
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        MsgText.Text = "";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.GetLicenseAsync();
            ApplyStatus(st);
        }
        catch (Exception ex)
        {
            StatusTitle.Text = "Engine offline";
            StatusDetail.Text = "Start the engine from Settings, then refresh.";
            PriceLine.Text = "";
            TechText.Text = ex.Message;
        }
    }

    private void ApplyStatus(JsonElement st)
    {
        var licensed = st.TryGetProperty("licensed", out var lic) && lic.ValueKind == JsonValueKind.True;
        var enforce = st.TryGetProperty("enforce", out var en) && en.ValueKind == JsonValueKind.True;
        var mode = JsonUi.Str(st, "mode");
        var gate = JsonUi.Str(st, "gate");
        var price = st.TryGetProperty("price_usd", out var p) && p.TryGetDouble(out var pd)
            ? pd.ToString("0.00")
            : "49.99";

        if (!enforce)
        {
            StatusTitle.Text = licensed
                ? "Open-source build — unlocked"
                : "Not licensed";
            StatusDetail.Text =
                "This build does not require a purchase (FOS_LICENSE_ENFORCE off). " +
                "You can still activate a key to test the commercial path. " +
                JsonUi.Str(st, "buy_hint");
        }
        else if (licensed)
        {
            StatusTitle.Text = "Licensed";
            var plan = JsonUi.Str(st, "plan");
            var source = JsonUi.Str(st, "source");
            StatusDetail.Text =
                $"Plan: {plan}. Source: {source}. " +
                "This device is activated. Use the same key on Mac / mobile official builds.";
        }
        else
        {
            StatusTitle.Text = "Activation required";
            StatusDetail.Text = JsonUi.Str(st, "activate_hint") + " " + JsonUi.Str(st, "buy_hint");
        }

        PriceLine.Text = $"List price: ${price} USD one-time · mode={mode} · gate={gate}";

        var email = JsonUi.Str(st, "email");
        if (!string.IsNullOrEmpty(email) && string.IsNullOrWhiteSpace(EmailBox.Text))
            EmailBox.Text = email;

        TechText.Text =
            $"device: {JsonUi.Str(st, "device_id")}\n" +
            $"license_id: {JsonUi.Str(st, "license_id")}\n" +
            $"last_verified: {JsonUi.Str(st, "last_verified_at")}\n" +
            $"grace_days: {JsonUi.Str(st, "grace_days")} · server: {(st.TryGetProperty("server_configured", out var sc) && sc.ValueKind == JsonValueKind.True ? JsonUi.Str(st, "server_url") : "local-only")}";
    }

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
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            try
            {
                var st = await api.RefreshLicenseAsync();
                ApplyStatus(st);
                MsgText.Text = JsonUi.Str(st, "message");
                if (string.IsNullOrEmpty(MsgText.Text))
                    MsgText.Text = "Status updated.";
            }
            catch
            {
                await LoadAsync();
                MsgText.Text = "Status reloaded.";
            }
        }
        catch (Exception ex)
        {
            MsgText.Text = ex.Message;
        }
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
            MsgText.Text = "Local license cleared.";
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
