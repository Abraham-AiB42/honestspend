using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class AboutPage : Page
{
    public AboutPage()
    {
        InitializeComponent();
    }

    private void GoLicense_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("license");
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var info = await api.GetSystemInfoAsync();
            var ver = JsonUi.Str(info, "version");
            VersionText.Text =
                $"Engine v{ver} · {JsonUi.Str(info, "product")} · " +
                $"{JsonUi.Str(info, "host")}:{JsonUi.Str(info, "port")}";
            var is10 = ver.StartsWith("1.") || ver.StartsWith("1");
            MaturityText.Text = is10
                ? $"HonestSpend {ver} — local liquidity OS. Not a bureau, not e-file, not payroll. See docs/RELEASE_1.0.0.md."
                : $"Feature tag v{ver} · pre-1.0 · maturity ~65% dream (~0.6.5-class). 1.0 only after docs/RC_1.0.md.";
            PathsText.Text =
                $"Data: {JsonUi.Str(info, "data_dir")}\nDB: {JsonUi.Str(info, "db_path")} ({JsonUi.Str(info, "db_size_mb")} MB)\n" +
                $"Grok {(info.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "enabled" : "off")} · " +
                $"Plaid {(info.TryGetProperty("plaid_enabled", out var p) && p.GetBoolean() ? "enabled" : "off")}";

            var licLine = "MIT source · Activate license for commercial / multi-device path";
            try
            {
                var lic = await api.GetLicenseAsync();
                var enforce = lic.TryGetProperty("enforce", out var en) && en.GetBoolean();
                var licensed = lic.TryGetProperty("licensed", out var l) && l.GetBoolean();
                var price = lic.TryGetProperty("price_usd", out var pr) && pr.TryGetDouble(out var pd)
                    ? pd.ToString("0.00")
                    : "49.99";
                licLine = enforce
                    ? (licensed
                        ? $"Licensed · ${price} lifetime personal · MIT source available"
                        : $"Commercial build · ${price} one-time · Activate license")
                    : $"OSS build unlocked · store list price ${price} · MIT License";
            }
            catch
            {
                /* engine partial */
            }
            LicenseText.Text = licLine;
        }
        catch (Exception ex)
        {
            VersionText.Text = "Engine offline — start from Settings.";
            MaturityText.Text = "Start the engine to see version.";
            PathsText.Text = ex.Message;
        }
    }
}
