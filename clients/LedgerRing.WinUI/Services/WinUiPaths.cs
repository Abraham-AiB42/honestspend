using System.Diagnostics;
using System.Text.Json;

namespace LedgerRing_WinUI.Services;

/// <summary>Locate this WinUI EXE for tray / package cold-start (client-first).</summary>
public static class WinUiPaths
{
    /// <summary>Write path so Python tray can launch us via LEDGERRING_WINUI or pointer file.</summary>
    public static void PublishExePathForTray()
    {
        try
        {
            var exe = Environment.ProcessPath
                ?? Process.GetCurrentProcess().MainModule?.FileName;
            if (string.IsNullOrWhiteSpace(exe) || !File.Exists(exe))
                return;

            var data = !string.IsNullOrWhiteSpace(AppConfig.DataDir)
                ? AppConfig.DataDir!
                : Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    ".financial-os");
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
}
