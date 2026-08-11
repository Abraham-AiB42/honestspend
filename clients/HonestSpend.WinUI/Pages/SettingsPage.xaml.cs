using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;

namespace HonestSpend_WinUI.Pages;

public sealed partial class SettingsPage : Page
{
    private readonly Dictionary<int, NumberBox> _acctBufferBoxes = new();
    private string _plaidLinkUrl = "http://127.0.0.1:7420/static/plaid-link.html";

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
        RefreshTaskStatus();
        RefreshAppLockStatus();
        await LoadPathsAsync();
        await LoadFiscalAsync();
        await LoadImportReminderAsync();
        await LoadByokAsync();
        await LoadAccountBuffersAsync();
    }

    private async Task LoadByokAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.GetPlaidStatusAsync();
            var enabled = st.TryGetProperty("enabled", out var en) && en.GetBoolean();
            var n = JsonUi.Int(st, "item_count", 0);
            var limit = JsonUi.Int(st, "item_limit", 10);
            _plaidLinkUrl = JsonUi.Str(st, "link_url", _plaidLinkUrl);
            PlaidStatusText.Text = enabled
                ? $"Plaid ON · env {JsonUi.Str(st, "env")} · institutions {n}/{limit}"
                : "Plaid OFF — paste client_id + secret below (stored locally).";
            if (st.TryGetProperty("credentials", out var cred) && cred.ValueKind == JsonValueKind.Object)
            {
                var masked = JsonUi.Str(cred, "client_id_masked");
                if (!string.IsNullOrEmpty(masked) && masked != "—")
                    PlaidClientIdBox.PlaceholderText = masked;
                SelectTag(PlaidEnvBox, JsonUi.Str(cred, "env", "sandbox"));
            }
            var ai = await api.GetAiCredentialsAsync();
            var lines = new List<string>();
            if (ai.TryGetProperty("providers", out var prov) && prov.ValueKind == JsonValueKind.Array)
            {
                foreach (var p in prov.EnumerateArray())
                {
                    if (p.TryGetProperty("configured", out var cf) && cf.GetBoolean())
                        lines.Add($"{JsonUi.Str(p, "label")}: set");
                }
            }
            ByokStatusText.Text = lines.Count > 0
                ? "AI: " + string.Join(" · ", lines)
                : "AI: none configured (optional).";
        }
        catch (Exception ex)
        {
            PlaidStatusText.Text = "BYOK: start engine to load. " + ex.Message;
        }
    }

    private async Task LoadAccountBuffersAsync()
    {
        AccountBuffersPanel.Children.Clear();
        _acctBufferBoxes.Clear();
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var buf = await api.GetSetupBuffersAsync();
            if (!buf.TryGetProperty("accounts", out var accs) || accs.ValueKind != JsonValueKind.Array)
                return;
            foreach (var a in accs.EnumerateArray())
            {
                var id = JsonUi.Int(a, "id", 0);
                var cur = 0.0;
                var sb = JsonUi.Str(a, "safety_buffer", "0");
                if (sb is not ("—" or ""))
                    double.TryParse(sb, out cur);
                var nb = new NumberBox
                {
                    Header = $"{JsonUi.Str(a, "nickname")} buffer ($)",
                    Value = cur,
                    Minimum = 0,
                };
                if (id > 0) _acctBufferBoxes[id] = nb;
                AccountBuffersPanel.Children.Add(nb);
            }
        }
        catch
        {
            /* optional */
        }
    }

    private async void SavePlaidKeys_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var id = PlaidClientIdBox.Text?.Trim() ?? "";
            var secret = PlaidSecretBox.Password?.Trim() ?? "";
            if (string.IsNullOrEmpty(id) || string.IsNullOrEmpty(secret))
                throw new InvalidOperationException("Enter client_id and secret.");
            var env = TagOf(PlaidEnvBox) ?? "sandbox";
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.SavePlaidCredentialsAsync(id, secret, env);
            ByokStatusText.Text = "Plaid keys saved (local).";
            await LoadByokAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void OpenPlaidLink_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo(_plaidLinkUrl) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void SaveAiKey_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var key = AiKeyBox.Text?.Trim() ?? "";
            if (string.IsNullOrEmpty(key))
                throw new InvalidOperationException("Enter an API key.");
            var provider = TagOf(AiProviderBox) ?? "xai";
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.SaveAiCredentialsAsync(provider, key);
            AiKeyBox.Text = "";
            ByokStatusText.Text = $"Saved {provider} key locally.";
            await LoadByokAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void SaveBuffersAndFiscal_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var total = double.IsNaN(BufferBox.Value) ? 1000m : (decimal)BufferBox.Value;
            var acct = new List<object>();
            foreach (var kv in _acctBufferBoxes)
            {
                if (double.IsNaN(kv.Value.Value)) continue;
                acct.Add(new { id = kv.Key, safety_buffer = (decimal)kv.Value.Value });
            }
            await api.SaveSetupBuffersAsync(new { total_buffer = total, account_buffers = acct });
            SaveFiscal_Click(sender, e);
            StatusText.Text = "Safety buffers saved.";
            await LoadAccountBuffersAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task LoadImportReminderAsync()
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var s = await api.GetSettingsAsync();
            SelectTag(ImportCadenceBox, JsonUi.Str(s, "import_reminder_cadence", "weekly"));
            SelectTag(ImportFocusBox, JsonUi.Str(s, "import_reminder_focus", "transactions"));
            var rem = await api.GetImportReminderAsync();
            var due = rem.TryGetProperty("due", out var d) && d.GetBoolean();
            ImportReminderStatusText.Text = due
                ? (JsonUi.Str(rem, "title") + " — " + JsonUi.Str(rem, "reason"))
                : ("On track · cadence " + JsonUi.Str(rem, "cadence") +
                   (string.IsNullOrEmpty(JsonUi.Str(rem, "last_import_at", ""))
                       ? " · never imported yet"
                       : " · last " + JsonUi.Str(rem, "last_import_at")));
        }
        catch (Exception ex)
        {
            ImportReminderStatusText.Text = "Money-in: start engine to load. " + ex.Message;
        }
    }

    private async void SaveImportReminder_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.PatchSettingsAsync(new Dictionary<string, object?>
            {
                ["import_reminder_cadence"] = TagOf(ImportCadenceBox) ?? "weekly",
                ["import_reminder_focus"] = TagOf(ImportFocusBox) ?? "transactions",
            });
            StatusText.Text = "Money-in reminder settings saved.";
            await LoadImportReminderAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void SnoozeImport_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.SnoozeImportReminderAsync(7);
            StatusText.Text = "Import reminder snoozed 7 days.";
            await LoadImportReminderAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void AckImport_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.AckImportReminderAsync();
            StatusText.Text = "Marked books as refreshed (reminder clock reset).";
            await LoadImportReminderAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void RefreshTasks_Click(object sender, RoutedEventArgs e) => RefreshTaskStatus();

    /// <summary>Query Task Scheduler for HonestSpend-AutoBackup / HonestSpend-Digest (current user).</summary>
    private void RefreshTaskStatus()
    {
        try
        {
            // One-shot PowerShell: State + LastRunTime + NextRunTime for known task names
            const string ps =
                "$names=@('HonestSpend-AutoBackup','HonestSpend-Digest','HonestSpend-ImportInbox');" +
                "foreach($n in $names){" +
                "  $t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;" +
                "  if(-not $t){ Write-Output ($n + '|missing|—|—'); continue }" +
                "  $i=$t | Get-ScheduledTaskInfo;" +
                "  $last=if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')}else{'never'};" +
                "  $next=if($i.NextRunTime -and $i.NextRunTime.Year -gt 2000){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}else{'—'};" +
                "  Write-Output ($n + '|' + $t.State + '|' + $last + '|' + $next)" +
                "}";
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = $"-NoProfile -NonInteractive -Command \"{ps}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var p = System.Diagnostics.Process.Start(psi);
            if (p is null)
            {
                TaskStatusText.Text = "Could not start PowerShell to query tasks.";
                return;
            }
            var stdout = p.StandardOutput.ReadToEnd();
            p.WaitForExit(15000);
            var lines = new List<string>();
            foreach (var raw in stdout.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = raw.Split('|');
                if (parts.Length < 4)
                {
                    lines.Add(raw.Trim());
                    continue;
                }
                var name = parts[0] switch
                {
                    "HonestSpend-AutoBackup" => "Auto-backup",
                    "HonestSpend-Digest" => "Daily digest",
                    "HonestSpend-ImportInbox" => "Import inbox",
                    _ => parts[0],
                };
                var state = parts[1];
                if (state.Equals("missing", StringComparison.OrdinalIgnoreCase))
                    lines.Add($"· {name}: not registered");
                else
                    lines.Add($"· {name}: {state} · last {parts[2]} · next {parts[3]}");
            }
            TaskStatusText.Text = lines.Count > 0
                ? string.Join("\n", lines)
                : "No task info returned. Register tasks below if needed.";
        }
        catch (Exception ex)
        {
            TaskStatusText.Text = "Task status unavailable: " + ex.Message;
        }
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
            var target = Path.Combine(od, "HonestSpend", "data");
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

    private async void InstallEngine_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        StatusText.Text = "Installing / repairing engine…";
        try
        {
            var root = await Task.Run(() =>
            {
                // Force re-extract if zip present: delete incomplete LocalAppData install
                var zip = EngineBootstrap.FindEnginePortableZip();
                if (zip is not null && Directory.Exists(EngineBootstrap.LocalEngineRoot)
                    && !BackendHost.LooksLikeEngine(EngineBootstrap.LocalEngineRoot))
                {
                    try { Directory.Delete(EngineBootstrap.LocalEngineRoot, true); } catch { /* ignore */ }
                }
                return EngineBootstrap.EnsureEngineAvailable(out var msg) is string eng
                    ? (eng, msg)
                    : (null as string, msg);
            });
            if (root.Item1 is null)
            {
                StatusText.Text = root.Item2 ?? "Engine install failed.";
                ErrorBar.Message = StatusText.Text;
                ErrorBar.IsOpen = true;
                return;
            }
            BackendRootBox.Text = root.Item1;
            AppConfig.BackendRoot = root.Item1;
            StatusText.Text = root.Item2 + " · starting…";
            await StartEngine_Click_Core();
        }
        catch (Exception ex)
        {
            StatusText.Text = ex.Message;
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void RefreshAppLockStatus()
    {
        try
        {
            var mode = AppLockService.Mode;
            AppLockStatusText.Text = mode switch
            {
                AppLockService.LockMode.None => "Current: no lock",
                AppLockService.LockMode.Pin => "Current: PIN",
                AppLockService.LockMode.Password => "Current: password",
                AppLockService.LockMode.Platform => "Current: Windows Hello",
                _ => "Current: unknown",
            };
        }
        catch
        {
            AppLockStatusText.Text = "App lock status unavailable";
        }
    }

    private async void AppLockNone_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            AppLockService.SetNone();
            AppLockMsgText.Text =
                "App lock UI disabled. If books were encrypted, use Clear lock with your PIN to also decrypt, or leave sealed.";
            RefreshAppLockStatus();
        }
        catch (Exception ex)
        {
            AppLockMsgText.Text = ex.Message;
        }
        await Task.CompletedTask;
    }

    private async void AppLockPin_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var pinBox = new PasswordBox { Header = "PIN (4–8 digits)", MaxLength = 8 };
            var confBox = new PasswordBox { Header = "Confirm PIN", MaxLength = 8 };
            var panel = new StackPanel { Spacing = 8 };
            panel.Children.Add(new TextBlock
            {
                Text = "Also encrypts books at rest (AES-256). Forget this PIN and sealed books cannot be recovered.",
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.85,
            });
            panel.Children.Add(pinBox);
            panel.Children.Add(confBox);
            var dlg = new ContentDialog
            {
                Title = "Set app PIN + encrypt books",
                Content = panel,
                PrimaryButtonText = "Save",
                CloseButtonText = "Cancel",
                XamlRoot = XamlRoot,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary)
                return;
            if (pinBox.Password != confBox.Password)
                throw new InvalidOperationException("PIN confirmation does not match.");
            AppLockService.SetPin(pinBox.Password);
            await AppLockService.EnableDatabaseEncryptionAsync(pinBox.Password, "pin", "password");
            AppLockMsgText.Text = "PIN saved and database encryption enabled.";
            RefreshAppLockStatus();
        }
        catch (Exception ex)
        {
            AppLockMsgText.Text = ex.Message;
        }
    }

    private async void AppLockPassword_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var passBox = new PasswordBox { Header = "Password (min 8)" };
            var confBox = new PasswordBox { Header = "Confirm password" };
            var panel = new StackPanel { Spacing = 8 };
            panel.Children.Add(new TextBlock
            {
                Text = "Also encrypts books at rest. Not your bank password.",
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.85,
            });
            panel.Children.Add(passBox);
            panel.Children.Add(confBox);
            var dlg = new ContentDialog
            {
                Title = "Set app password + encrypt books",
                Content = panel,
                PrimaryButtonText = "Save",
                CloseButtonText = "Cancel",
                XamlRoot = XamlRoot,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary)
                return;
            if (passBox.Password != confBox.Password)
                throw new InvalidOperationException("Password confirmation does not match.");
            AppLockService.SetPassword(passBox.Password);
            await AppLockService.EnableDatabaseEncryptionAsync(passBox.Password, "password", "password");
            AppLockMsgText.Text = "Password saved and database encryption enabled.";
            RefreshAppLockStatus();
        }
        catch (Exception ex)
        {
            AppLockMsgText.Text = ex.Message;
        }
    }

    private async void AppLockHello_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (!await AppLockService.IsWindowsHelloAvailableAsync())
                throw new InvalidOperationException("Windows Hello is not available on this device.");
            var ok = await AppLockService.TryWindowsHelloAsync("Confirm Windows Hello for HonestSpend");
            if (!ok)
                throw new InvalidOperationException("Windows Hello cancelled.");
            AppLockService.SetPlatform("windows_hello");
            await AppLockService.EnableDatabaseEncryptionAsync(null, "platform", "client");
            AppLockMsgText.Text = "Windows Hello lock + device-bound encryption key enabled.";
            RefreshAppLockStatus();
        }
        catch (Exception ex)
        {
            AppLockMsgText.Text = ex.Message;
        }
    }

    private async void AppLockClear_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var pinBox = new PasswordBox { Header = "Current PIN/password (if encrypted)" };
            var panel = new StackPanel { Spacing = 8 };
            panel.Children.Add(new TextBlock
            {
                Text = "Clears UI lock. If books are encrypted, enter PIN/password to decrypt to plaintext.",
                TextWrapping = TextWrapping.Wrap,
            });
            panel.Children.Add(pinBox);
            var dlg = new ContentDialog
            {
                Title = "Clear app lock",
                Content = panel,
                PrimaryButtonText = "Clear",
                CloseButtonText = "Cancel",
                XamlRoot = XamlRoot,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary)
                return;
            var secret = pinBox.Password;
            if (!string.IsNullOrEmpty(secret))
            {
                await AppLockService.UnlockDatabaseAsync(secret);
                try { await AppLockService.DisableDatabaseEncryptionAsync(secret); }
                catch { /* may already be off */ }
            }
            AppLockService.ClearLock();
            AppLockService.SetNone();
            AppLockMsgText.Text = "Lock cleared. Books were not deleted.";
            RefreshAppLockStatus();
        }
        catch (Exception ex)
        {
            AppLockMsgText.Text = ex.Message;
        }
    }

    private async void StartEngine_Click(object sender, RoutedEventArgs e)
        => await StartEngine_Click_Core();

    private async Task StartEngine_Click_Core()
    {
        Save_Click(this, new RoutedEventArgs());
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
                ? "Tray started — hover for Safe to spend; Open HonestSpend opens this desktop app."
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

    private void RegisterTasks_Click(object sender, RoutedEventArgs e)
        => RunTaskScript(uninstall: false);

    private void UnregisterTasks_Click(object sender, RoutedEventArgs e)
        => RunTaskScript(uninstall: true);

    private void RunTaskScript(bool uninstall)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var root = BackendHost.ResolveBackendRoot()
                ?? AppConfig.BackendRoot
                ?? Directory.GetCurrentDirectory();
            var script = Path.Combine(root, "scripts", "register-tasks.ps1");
            // Package layout: scripts next to engine or repo root
            if (!File.Exists(script))
                script = Path.Combine(root, "..", "scripts", "register-tasks.ps1");
            script = Path.GetFullPath(script);
            if (!File.Exists(script))
            {
                AutomationStatusText.Text =
                    "register-tasks.ps1 not found next to the engine. Use a full repo or package that includes scripts\\.";
                return;
            }
            var args = uninstall
                ? $"-NoProfile -ExecutionPolicy Bypass -File \"{script}\" -Uninstall"
                : $"-NoProfile -ExecutionPolicy Bypass -File \"{script}\"";
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = args,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var p = System.Diagnostics.Process.Start(psi);
            if (p is null)
            {
                AutomationStatusText.Text = "Could not start PowerShell.";
                return;
            }
            var stdout = p.StandardOutput.ReadToEnd();
            var stderr = p.StandardError.ReadToEnd();
            p.WaitForExit(60000);
            AutomationStatusText.Text = p.ExitCode == 0
                ? (uninstall ? "Scheduled tasks removed." : "Scheduled tasks registered (current user).")
                  + (string.IsNullOrWhiteSpace(stdout) ? "" : "\n" + stdout.Trim())
                : $"Task script exit {p.ExitCode}: {stderr.Trim()} {stdout.Trim()}";
            RefreshTaskStatus();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

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

            BudgetReserveBox.IsChecked = !s.TryGetProperty("budget_reserve_enabled", out var br)
                || br.ValueKind != JsonValueKind.False;
            var wstart = JsonUi.Int(s, "budget_week_starts_on", 0);
            SelectTag(WeekStartBox, wstart.ToString());
            var mask = JsonUi.Int(s, "budget_workdays", 31);
            WdMon.IsChecked = (mask & 1) != 0;
            WdTue.IsChecked = (mask & 2) != 0;
            WdWed.IsChecked = (mask & 4) != 0;
            WdThu.IsChecked = (mask & 8) != 0;
            WdFri.IsChecked = (mask & 16) != 0;
            WdSat.IsChecked = (mask & 32) != 0;
            WdSun.IsChecked = (mask & 64) != 0;
        }
        catch (Exception ex)
        {
            StatusText.Text += " · fiscal: " + ex.Message;
        }
    }

    private int WorkdayMaskFromUi()
    {
        var m = 0;
        if (WdMon.IsChecked == true) m |= 1;
        if (WdTue.IsChecked == true) m |= 2;
        if (WdWed.IsChecked == true) m |= 4;
        if (WdThu.IsChecked == true) m |= 8;
        if (WdFri.IsChecked == true) m |= 16;
        if (WdSat.IsChecked == true) m |= 32;
        if (WdSun.IsChecked == true) m |= 64;
        return m == 0 ? 31 : m;
    }

    private async void SaveBudgetSettings_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var weekStart = 0;
            if (int.TryParse(TagOf(WeekStartBox), out var ws))
                weekStart = ws;
            await api.PatchSettingsAsync(new Dictionary<string, object?>
            {
                ["budget_reserve_enabled"] = BudgetReserveBox.IsChecked == true,
                ["budget_week_starts_on"] = weekStart,
                ["budget_workdays"] = WorkdayMaskFromUi(),
            });
            StatusText.Text = "Budget workweek & reserve settings saved.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
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
