using Microsoft.Win32;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// HKCU Run key — launch HonestSpend at Windows logon (no admin required).
/// </summary>
public static class StartupLaunch
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "HonestSpend";

    public static string? CurrentCommand
    {
        get
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: false);
                return key?.GetValue(ValueName) as string;
            }
            catch
            {
                return null;
            }
        }
    }

    public static bool IsEnabled => !string.IsNullOrWhiteSpace(CurrentCommand);

    /// <summary>
    /// Register this EXE to start at logon.
    /// Prefer <paramref name="trayOnly"/> so logon starts engine + tray without a big window.
    /// </summary>
    public static void Enable(string? exePath = null, string? arguments = null, bool trayOnly = true)
    {
        exePath ??= Environment.ProcessPath
            ?? Path.Combine(AppContext.BaseDirectory, "HonestSpend.WinUI.exe");
        if (!File.Exists(exePath))
            throw new FileNotFoundException("App executable not found for startup registration.", exePath);

        if (string.IsNullOrWhiteSpace(arguments))
            arguments = trayOnly ? "--tray-only" : null;

        var cmd = string.IsNullOrWhiteSpace(arguments)
            ? $"\"{exePath}\""
            : $"\"{exePath}\" {arguments}";

        using var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: true)
            ?? Registry.CurrentUser.CreateSubKey(RunKey);
        key.SetValue(ValueName, cmd);
        // drop prior brand logon keys so only HonestSpend starts
        key.DeleteValue("HonestSpend", throwOnMissingValue: false);
        key.DeleteValue("HonestSpend", throwOnMissingValue: false);
    }

    public static void Disable()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
            key?.DeleteValue(ValueName, throwOnMissingValue: false);
            // prior brand logon keys
            key?.DeleteValue("HonestSpend", throwOnMissingValue: false);
            key?.DeleteValue("HonestSpend", throwOnMissingValue: false);
        }
        catch
        {
            /* ignore */
        }
    }
}
