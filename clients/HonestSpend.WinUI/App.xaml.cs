using HonestSpend_WinUI.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.Storage;

namespace HonestSpend_WinUI;

public partial class App : Application
{
    private Window? _window;
    private DispatcherQueue? _dispatcher;

    /// <summary>Shared host for the Python fiscal engine process.</summary>
    public static BackendHost? Backend { get; set; }

    /// <summary>Active main window (for pickers / HWND interop).</summary>
    public static Window? MainWindowInstance { get; private set; }

    public App()
    {
        InitializeComponent();
        try
        {
            AppConfig.ApplyCommandLine(Environment.GetCommandLineArgs());
        }
        catch
        {
            /* ignore */
        }
        LoadConfig();
        Backend = new BackendHost();
        UnhandledException += (_, e) =>
        {
            try
            {
                var dir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    ".financial-os");
                Directory.CreateDirectory(dir);
                var path = Path.Combine(dir, "winui-crash.log");
                File.AppendAllText(
                    path,
                    $"[{DateTime.Now:O}] {e.Message}\n{e.Exception}\n\n");
            }
            catch
            {
                /* ignore log failures */
            }
            e.Handled = true;
        };
    }

    private static void LoadConfig()
    {
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            if (ls["BaseUrl"] is string url && !string.IsNullOrWhiteSpace(url))
                AppConfig.BaseUrl = url;
            if (ls["ApiKey"] is string key && !string.IsNullOrWhiteSpace(key))
                AppConfig.ApiKey = key;
            if (ls["BackendRoot"] is string root && !string.IsNullOrWhiteSpace(root))
                AppConfig.BackendRoot = root;
            if (ls["DataDir"] is string data && !string.IsNullOrWhiteSpace(data))
                AppConfig.DataDir = data;
            if (ls["StartTrayWithApp"] is bool tray)
                AppConfig.StartTrayWithApp = tray || AppConfig.StartTrayWithApp;
            if (ls["StartMinimized"] is bool min)
                AppConfig.StartMinimized = min || AppConfig.StartMinimized;
        }
        catch
        {
            /* unpackaged / first run */
        }
    }

    /// <summary>Seal books while engine is still alive, then kill the process.</summary>
    private static void SealThenStopEngine()
    {
        try
        {
            // Always attempt seal when encryption may be on (not only UI lock mode)
            AppLockService.SealDatabaseAsync().GetAwaiter().GetResult();
        }
        catch { /* ignore */ }
        try { Backend?.Dispose(); } catch { /* ignore */ }
        Backend = null;
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        if (!SingleInstance.TryAcquire())
        {
            // Another instance was signaled to show; exit this process.
            // (Instant close if you double-open — the first window should pop forward.)
            Environment.Exit(0);
            return;
        }

        // Release mutex if process dies cleanly so the next launch is not blocked.
        AppDomain.CurrentDomain.ProcessExit += (_, _) =>
        {
            try { SealThenStopEngine(); } catch { /* ignore */ }
            try { SingleInstance.Release(); } catch { /* ignore */ }
        };

        _dispatcher = DispatcherQueue.GetForCurrentThread();
        SingleInstance.StartShowListener(() =>
        {
            _dispatcher?.TryEnqueue(() => ShowMainWindow());
        });

        _window = new MainWindow();
        MainWindowInstance = _window;
        _window.Closed += (_, _) =>
        {
            try
            {
                var dir = WinUiPaths.DataDirRoot();
                Directory.CreateDirectory(dir);
                File.AppendAllText(
                    Path.Combine(dir, "winui-lifecycle.log"),
                    $"[{DateTime.Now:O}] [pid={Environment.ProcessId}] MainWindow.Closed\n");
            }
            catch { /* ignore */ }
            try { SealThenStopEngine(); } catch { /* ignore */ }
            try { SingleInstance.Release(); } catch { /* ignore */ }
        };

        if (AppConfig.TrayOnly || AppConfig.StartMinimized)
        {
            try
            {
                if (AppConfig.TrayOnly)
                    _window.AppWindow.Hide();
                else if (_window.AppWindow.Presenter is OverlappedPresenter op)
                    op.Minimize();
            }
            catch
            {
                /* presenter may not be ready */
            }
        }

        _window.Activate();

        if (AppConfig.TrayOnly)
        {
            try { _window.AppWindow.Hide(); } catch { /* ignore */ }
        }
        else if (AppConfig.StartMinimized)
        {
            try
            {
                if (_window.AppWindow.Presenter is OverlappedPresenter op)
                    op.Minimize();
            }
            catch { /* ignore */ }
        }

        // So tray / scripts can re-open this native client (not Glance/PWA)
        WinUiPaths.PublishExePathForTray();

        if (Backend is not null)
        {
            _ = Task.Run(async () =>
            {
                try
                {
                    await Backend.EnsureRunningAsync();
                    WinUiPaths.PublishExePathForTray();
                    if (AppConfig.StartTrayWithApp || AppConfig.TrayOnly)
                        TrayHost.TryStart();
                }
                catch
                {
                    /* Settings can start engine/tray manually */
                }
            });
        }
    }

    /// <summary>Show main window (second launch, tray, or Settings). Honors winui.navigate deep-link.</summary>
    public static void ShowMainWindow()
    {
        if (MainWindowInstance is null) return;
        try
        {
            MainWindowInstance.AppWindow.Show();
            MainWindowInstance.Activate();
            if (MainWindowInstance.AppWindow.Presenter is OverlappedPresenter op)
                op.Restore();
            if (MainWindowInstance is MainWindow mw)
                mw.ConsumePendingNavigation();
        }
        catch
        {
            /* ignore */
        }
    }
}
