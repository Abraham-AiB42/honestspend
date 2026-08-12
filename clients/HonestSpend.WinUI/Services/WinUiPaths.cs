using System.Diagnostics;

namespace HonestSpend_WinUI.Services;

/// <summary>Locate this WinUI EXE for tray / package cold-start (client-first).</summary>
public static class WinUiPaths
{
    public const string PreferredDataDirName = ".HonestSpend";
    public const string LegacyDataDirName = ".financial-os";

    /// <summary>
    /// Default on-disk data folder — always ~/.HonestSpend (never the old financial-os path).
    /// Legacy books appear as a separate setup choice when that folder still exists.
    /// </summary>
    public static string DefaultLocalDataDir()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            PreferredDataDirName);
    }

    public static string LegacyLocalDataDir()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            LegacyDataDirName);
    }

    /// <summary>Default data folder used when AppConfig.DataDir is unset.</summary>
    public static string DataDirRoot()
    {
        if (!string.IsNullOrWhiteSpace(AppConfig.DataDir))
            return AppConfig.DataDir!;
        return DefaultLocalDataDir();
    }

    /// <summary>Pending page tag from tray / second launch (e.g. review, reports).</summary>
    public static string NavigateRequestPath()
        => Path.Combine(DataDirRoot(), "winui.navigate");

    /// <summary>Write path so Python tray can launch us via HONESTSPEND_WINUI or pointer file.</summary>
    public static void PublishExePathForTray()
    {
        try
        {
            var exe = Environment.ProcessPath
                ?? Process.GetCurrentProcess().MainModule?.FileName;
            if (string.IsNullOrWhiteSpace(exe) || !File.Exists(exe))
                return;

            var data = DataDirRoot();
            Directory.CreateDirectory(data);
            var pointer = Path.Combine(data, "winui.path");
            File.WriteAllText(pointer, exe);

            // Also next to engine so package layout is self-describing
            var root = BackendHost.ResolveBackendRoot();
            if (root is not null)
            {
                try
                {
                    File.WriteAllText(Path.Combine(root, "winui.path"), exe);
                    var parent = Directory.GetParent(root)?.FullName;
                    if (parent is not null)
                        File.WriteAllText(Path.Combine(parent, "winui.path"), exe);
                }
                catch { /* ignore */ }
            }
        }
        catch
        {
            /* non-fatal */
        }
    }

    /// <summary>Queue a nav tag for the running instance (or cold start).</summary>
    public static void WriteNavigateRequest(string pageTag)
    {
        try
        {
            var tag = (pageTag ?? "").Trim().ToLowerInvariant();
            if (string.IsNullOrEmpty(tag)) return;
            // normalize aliases
            tag = tag switch
            {
                "sort" or "charges" or "sort-charges" => "review",
                "settings" => "settings",
                _ => tag,
            };
            var dir = DataDirRoot();
            Directory.CreateDirectory(dir);
            File.WriteAllText(NavigateRequestPath(), tag);
        }
        catch
        {
            /* non-fatal */
        }
    }

    /// <summary>Read and clear pending nav tag, or null if none.</summary>
    public static string? ConsumeNavigateRequest()
    {
        try
        {
            var path = NavigateRequestPath();
            if (!File.Exists(path)) return null;
            var tag = File.ReadAllText(path).Trim().Split('\n', '\r')[0].Trim().ToLowerInvariant();
            try { File.Delete(path); } catch { /* ignore */ }
            return string.IsNullOrEmpty(tag) ? null : tag;
        }
        catch
        {
            return null;
        }
    }
}
