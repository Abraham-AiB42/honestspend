using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class AboutPage : Page
{
    public AboutPage()
    {
        InitializeComponent();
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
                ? $"LedgerRing {ver} — local liquidity OS. Not a bureau, not e-file, not payroll. See docs/RELEASE_1.0.0.md."
                : $"Feature tag v{ver} · pre-1.0 · maturity ~65% dream (~0.6.5-class). 1.0 only after docs/RC_1.0.md.";
            PathsText.Text =
                $"Data: {JsonUi.Str(info, "data_dir")}\nDB: {JsonUi.Str(info, "db_path")} ({JsonUi.Str(info, "db_size_mb")} MB)\n" +
                $"Grok {(info.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "enabled" : "off")} · " +
                $"Plaid {(info.TryGetProperty("plaid_enabled", out var p) && p.GetBoolean() ? "enabled" : "off")}";
            LicenseText.Text = is10
                ? "MIT License · freeware · v1.0 liquidity OS"
                : "MIT License · freeware · pre-1.0";
        }
        catch (Exception ex)
        {
            VersionText.Text = "Engine offline — start from Settings.";
            MaturityText.Text = "Start the engine to see version.";
            PathsText.Text = ex.Message;
        }
    }
}
