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
            VersionText.Text =
                $"Engine v{JsonUi.Str(info, "version")} · {JsonUi.Str(info, "product")} · " +
                $"{JsonUi.Str(info, "host")}:{JsonUi.Str(info, "port")}";
            PathsText.Text =
                $"Data: {JsonUi.Str(info, "data_dir")}\nDB: {JsonUi.Str(info, "db_path")} ({JsonUi.Str(info, "db_size_mb")} MB)\n" +
                $"Grok {(info.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "enabled" : "off")} · " +
                $"Plaid {(info.TryGetProperty("plaid_enabled", out var p) && p.GetBoolean() ? "enabled" : "off")}";
        }
        catch (Exception ex)
        {
            VersionText.Text = "Engine offline — start from Settings.";
            PathsText.Text = ex.Message;
        }
    }
}
