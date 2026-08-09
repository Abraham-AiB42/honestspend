using System.Globalization;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;

namespace LedgerRing_WinUI.Pages;

public sealed partial class SettingsPage : Page
{
    public SettingsPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        var ls = ApplicationData.Current.LocalSettings.Values;
        BaseUrlBox.Text = ls["BaseUrl"] as string ?? AppConfig.BaseUrl;
        ApiKeyBox.Password = ls["ApiKey"] as string ?? AppConfig.ApiKey ?? "";
        BackendRootBox.Text = ls["BackendRoot"] as string ?? AppConfig.BackendRoot ?? "";
        DataDirBox.Text = ls["DataDir"] as string ?? AppConfig.DataDir ?? "";
        TrayAutoBox.IsChecked = AppConfig.StartTrayWithApp;
        MinimizedBox.IsChecked = AppConfig.StartMinimized;
        LoginBox.IsChecked = StartupLaunch.IsEnabled;
        var root = BackendHost.ResolveBackendRoot();
        StatusText.Text =
            $"Backend root auto: {root ?? "(not found)"} · " +
            $"tray {(TrayHost.IsRunning ? "running" : "stopped")}";
        StartupStatusText.Text =
            (StartupLaunch.IsEnabled
                ? "Logon: ON · " + (StartupLaunch.CurrentCommand ?? "")
                : "Logon: off") +
            " · flags: --tray-only · --minimized · --tray · single-instance";
        EngineLogText.Text = App.Backend?.LogPath is string lp
            ? $"Engine log: {lp}"
            : "Engine log: ~/.financial-os/engine.log (after Start engine)";
        await LoadPathsAsync();
        await LoadFiscalAsync();
    }

    private async Task LoadPathsAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var paths = await api.GetSystemPathsAsync();
            var lines = new List<string>
            {
                $"Engine current: {JsonUi.Str(paths, "current")}",
                JsonUi.Str(paths, "hint"),
            };
            if (paths.TryGetProperty("candidates", out var c) && c.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in c.EnumerateArray().Take(6))
                    lines.Add($"· {JsonUi.Str(item, "label")}: {JsonUi.Str(item, "path")}");
            }
            PathsHintText.Text = string.Join("\n", lines);
        }
        catch (Exception ex)
        {
            PathsHintText.Text = "Paths: start engine to list OneDrive candidates. " + ex.Message;
        }
    }

    private void SuggestOneDrive_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            // Prefer env, then common paths — works offline without API
            string? od = Environment.GetEnvironmentVariable("OneDrive")
                ?? Environment.GetEnvironmentVariable("OneDriveConsumer")
                ?? Environment.GetEnvironmentVariable("OneDriveCommercial");
            if (string.IsNullOrWhiteSpace(od))
            {
                var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                foreach (var name in new[] { "OneDrive", "OneDrive - Personal" })
                {
                    var p = Path.Combine(home, name);
                    if (Directory.Exists(p)) { od = p; break; }
                }
            }
            if (string.IsNullOrWhiteSpace(od))
            {
                PathsHintText.Text = "OneDrive folder not found. Install OneDrive or paste a path manually.";
                return;
            }
            var target = Path.Combine(od, "LedgerRing", "data");
            DataDirBox.Text = target;
            PathsHintText.Text =
                $"Suggested: {target}\nSave connection, then restart engine so FOS_DATA_DIR applies. " +
                "Migrate via Data → backup/restore if you already have a DB.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void DefaultDataDir_Click(object sender, RoutedEventArgs e)
    {
        DataDirBox.Text = "";
        PathsHintText.Text = "Empty = engine default (~/.financial-os).";
    }

    private async void CopyDataDir_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var dest = DataDirBox.Text?.Trim();
            if (string.IsNullOrWhiteSpace(dest))
                throw new InvalidOperationException("Set a data dir path first (or Suggest OneDrive).");
            Directory.CreateDirectory(dest);

            // Source: current engine path if known, else default home
            var srcDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".financial-os");
            try
            {
                using var api = new LedgerApiClient();
                await api.EnsureBackendAsync();
                var info = await api.GetSystemInfoAsync();
                var p = JsonUi.Str(info, "data_dir", "");
                if (!string.IsNullOrWhiteSpace(p) && Directory.Exists(p))
                    srcDir = p;
            }
            catch
            {
                /* use default home */
            }

            var srcDb = Path.Combine(srcDir, "financial_os.db");
            var destDb = Path.Combine(dest, "financial_os.db");
            if (!File.Exists(srcDb))
                throw new InvalidOperationException($"No database at {srcDb}");

            var confirm = new ContentDialog
            {
                Title = "Copy database?",
                Content = $"From:\n{srcDb}\n\nTo:\n{destDb}\n\nThen save connection and restart engine.",
                PrimaryButtonText = "Copy",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = XamlRoot,
            };
            if (await confirm.ShowAsync() != ContentDialogResult.Primary) return;

            File.Copy(srcDb, destDb, overwrite: true);
            // also copy backups folder if present
            var srcBak = Path.Combine(srcDir, "backups");
            var destBak = Path.Combine(dest, "backups");
            if (Directory.Exists(srcBak))
            {
                Directory.CreateDirectory(destBak);
                foreach (var f in Directory.GetFiles(srcBak))
                    File.Copy(f, Path.Combine(destBak, Path.GetFileName(f)), overwrite: true);
            }

            AppConfig.DataDir = dest;
            ApplicationData.Current.LocalSettings.Values["DataDir"] = dest;
            PathsHintText.Text = $"Copied DB to {destDb}. Click Save connection, then Start engine to use FOS_DATA_DIR.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        AppConfig.BaseUrl = BaseUrlBox.Text.Trim();
        AppConfig.ApiKey = string.IsNullOrWhiteSpace(ApiKeyBox.Password) ? null : ApiKeyBox.Password.Trim();
        AppConfig.BackendRoot = string.IsNullOrWhiteSpace(BackendRootBox.Text) ? null : BackendRootBox.Text.Trim();
        AppConfig.DataDir = string.IsNullOrWhiteSpace(DataDirBox.Text) ? null : DataDirBox.Text.Trim();
        AppConfig.StartTrayWithApp = TrayAutoBox.IsChecked == true;
        AppConfig.StartMinimized = MinimizedBox.IsChecked == true;

        var ls = ApplicationData.Current.LocalSettings.Values;
        ls["BaseUrl"] = AppConfig.BaseUrl;
        ls["ApiKey"] = AppConfig.ApiKey ?? "";
        ls["BackendRoot"] = AppConfig.BackendRoot ?? "";
        ls["DataDir"] = AppConfig.DataDir ?? "";
        ls["StartTrayWithApp"] = AppConfig.StartTrayWithApp;
        ls["StartMinimized"] = AppConfig.StartMinimized;

        try
        {
            if (LoginBox.IsChecked == true)
                StartupLaunch.Enable(trayOnly: true);
            else
                StartupLaunch.Disable();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = "Startup registration: " + ex.Message;
            ErrorBar.IsOpen = true;
        }

        if (!string.IsNullOrWhiteSpace(AppConfig.DataDir))
        {
            try { Directory.CreateDirectory(AppConfig.DataDir); }
            catch { /* engine will mkdir too */ }
        }

        StatusText.Text = "Connection saved." +
            (AppConfig.StartTrayWithApp ? " Tray auto-start on." : "") +
            (StartupLaunch.IsEnabled ? " Logon tray-only on." : "") +
            (string.IsNullOrWhiteSpace(AppConfig.DataDir) ? "" : " Data dir set — restart engine to apply.");
        StartupStatusText.Text = StartupLaunch.IsEnabled
            ? "Logon: ON · " + (StartupLaunch.CurrentCommand ?? "")
            : "Logon: off";
    }

    private async void StartEngine_Click(object sender, RoutedEventArgs e)
    {
        Save_Click(sender, e);
        // Force new process so FOS_DATA_DIR is picked up
        try
        {
            App.Backend?.Dispose();
            App.Backend = new BackendHost();
        }
        catch { /* ignore */ }

        StatusText.Text = "Starting…";
        if (App.Backend is null)
        {
            StatusText.Text = "Backend host not available.";
            return;
        }
        var ok = await App.Backend.EnsureRunningAsync();
        StatusText.Text = ok
            ? "Engine healthy on " + AppConfig.BaseUrl +
              (string.IsNullOrWhiteSpace(AppConfig.DataDir) ? "" : " · FOS_DATA_DIR=" + AppConfig.DataDir)
            : ("Failed: " + (App.Backend.LastError ?? "unknown"));
        if (ok)
        {
            await LoadPathsAsync();
            await LoadFiscalAsync();
        }
    }

    private async void StartTray_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (App.Backend is not null)
                await App.Backend.EnsureRunningAsync();
            var ok = TrayHost.TryStart();
            StatusText.Text = ok
                ? "Tray process started (Spendable hover + critical toasts)."
                : "Could not start tray — check backend root and `pip install pystray pillow`.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void StopTray_Click(object sender, RoutedEventArgs e)
    {
        TrayHost.Stop();
        StatusText.Text = "Tray stop requested.";
    }

    private void ShowWindow_Click(object sender, RoutedEventArgs e) => App.ShowMainWindow();

    private async Task LoadFiscalAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var s = await api.GetSettingsAsync();

            SelectTag(ModeBox, JsonUi.Str(s, "ifpp_mode", "conservative"));
            BufferBox.Value = ParseD(s, "safety_buffer", 1000);
            SelectTag(ScopeBox, JsonUi.Str(s, "never_negative_scope", "checking"));
            SelectTag(NeverNegEnforceBox, JsonUi.Str(s, "never_negative_enforcement", "warn"));
            HorizonBox.Value = ParseD(s, "horizon_days", 45);
            OppRateBox.Value = ParseD(s, "opportunity_rate", double.NaN);
            OppAwareBox.IsChecked = s.TryGetProperty("opportunity_cost_aware", out var o) && o.GetBoolean();
            SelectTag(DebtBox, JsonUi.Str(s, "debt_strategy", "avalanche"));
            ExtraBox.Value = ParseD(s, "debt_extra_monthly", 0);
            AutoCatBox.IsChecked = !s.TryGetProperty("auto_categorize_on_import", out var ac) || ac.GetBoolean();
            ClearedOnlyBox.IsChecked = !s.TryGetProperty("ifpp_cleared_only", out var co) || co.GetBoolean();
            SelectTag(IfppScopeDefaultBox, JsonUi.Str(s, "ifpp_scope", "entity"));
        }
        catch (Exception ex)
        {
            StatusText.Text += " · fiscal: " + ex.Message;
        }
    }

    private async void SaveFiscal_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var cur = await api.GetSettingsAsync();
            var dict = new Dictionary<string, object?>();
            foreach (var p in cur.EnumerateObject())
            {
                dict[p.Name] = p.Value.ValueKind switch
                {
                    JsonValueKind.String => p.Value.GetString(),
                    JsonValueKind.Number => p.Value.GetDouble(),
                    JsonValueKind.True => true,
                    JsonValueKind.False => false,
                    JsonValueKind.Null => null,
                    _ => p.Value.GetRawText(),
                };
            }

            dict["ifpp_mode"] = TagOf(ModeBox) ?? "conservative";
            dict["safety_buffer"] = double.IsNaN(BufferBox.Value) ? 1000m : (decimal)BufferBox.Value;
            dict["never_negative_scope"] = TagOf(ScopeBox) ?? "checking";
            dict["never_negative_enforcement"] = TagOf(NeverNegEnforceBox) ?? "warn";
            dict["horizon_days"] = double.IsNaN(HorizonBox.Value) ? 45 : (int)HorizonBox.Value;
            dict["opportunity_cost_aware"] = OppAwareBox.IsChecked == true;
            dict["debt_strategy"] = TagOf(DebtBox) ?? "avalanche";
            dict["debt_extra_monthly"] = double.IsNaN(ExtraBox.Value) ? 0m : (decimal)ExtraBox.Value;
            dict["auto_categorize_on_import"] = AutoCatBox.IsChecked == true;
            dict["ifpp_cleared_only"] = ClearedOnlyBox.IsChecked == true;
            dict["ifpp_scope"] = TagOf(IfppScopeDefaultBox) ?? "entity";
            if (!double.IsNaN(OppRateBox.Value))
                dict["opportunity_rate"] = (decimal)OppRateBox.Value;

            await api.PatchSettingsAsync(new Dictionary<string, object?>
            {
                ["ifpp_mode"] = dict["ifpp_mode"],
                ["safety_buffer"] = dict["safety_buffer"],
                ["never_negative_scope"] = dict["never_negative_scope"],
                ["never_negative_enforcement"] = dict["never_negative_enforcement"],
                ["horizon_days"] = dict["horizon_days"],
                ["opportunity_cost_aware"] = dict["opportunity_cost_aware"],
                ["debt_strategy"] = dict["debt_strategy"],
                ["debt_extra_monthly"] = dict["debt_extra_monthly"],
                ["auto_categorize_on_import"] = dict["auto_categorize_on_import"],
                ["ifpp_cleared_only"] = dict["ifpp_cleared_only"],
                ["ifpp_scope"] = dict["ifpp_scope"],
                ["opportunity_rate"] = dict.GetValueOrDefault("opportunity_rate"),
            });
            StatusText.Text = "Fiscal settings saved (PATCH) · buffer, scope, cleared-only applied.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static void SelectTag(ComboBox box, string tag)
    {
        for (var i = 0; i < box.Items.Count; i++)
        {
            if (box.Items[i] is ComboBoxItem cbi && cbi.Tag as string == tag)
            {
                box.SelectedIndex = i;
                return;
            }
        }
        if (box.Items.Count > 0) box.SelectedIndex = 0;
    }

    private static string? TagOf(ComboBox box)
        => box.SelectedItem is ComboBoxItem cbi ? cbi.Tag as string : null;

    private static double ParseD(JsonElement s, string name, double fallback)
    {
        if (!s.TryGetProperty(name, out var el) || el.ValueKind == JsonValueKind.Null)
            return fallback;
        var raw = el.ValueKind == JsonValueKind.String ? el.GetString() : el.GetRawText();
        if (double.TryParse(raw, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return d;
        return fallback;
    }
}
