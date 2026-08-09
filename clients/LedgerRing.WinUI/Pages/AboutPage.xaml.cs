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
            // Feature tag may be 0.9.x while maturity is ~0.6.5-class — keep honest until 1.0 RC
            MaturityText.Text =
                $"Feature tag v{ver} · pre-1.0 · maturity ~65% dream (~0.6.5-class). " +
                "1.0 only after dogfood + package RC (docs/RC_1.0.md).";
            PathsText.Text =
                $"Data: {JsonUi.Str(info, "data_dir")}\nDB: {JsonUi.Str(info, "db_path")} ({JsonUi.Str(info, "db_size_mb")} MB)\n" +
                $"Grok {(info.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "enabled" : "off")} · " +
                $"Plaid {(info.TryGetProperty("plaid_enabled", out var p) && p.GetBoolean() ? "enabled" : "off")}";
        }
        catch (Exception ex)
        {
            VersionText.Text = "Engine offline — start from Settings.";
            MaturityText.Text = "Pre-1.0 · maturity ~65% dream (~0.6.5-class).";
            PathsText.Text = ex.Message;
        }
    }
}
