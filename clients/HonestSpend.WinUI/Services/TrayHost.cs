using System.Diagnostics;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// Starts the Python system-tray process (Safe to spend hover + digest toasts).
/// Uses tray.pid under data dir to avoid stacking trays.
/// </summary>
public static class TrayHost
{
    private static Process? _tray;
    private static readonly object Gate = new();

    public static bool IsRunning
    {
        get
        {
            lock (Gate)
            {
                if (_tray is { HasExited: false })
                    return true;
                return IsPidFileAlive();
            }
        }
    }

    private static string PidFilePath()
    {
        var dir = !string.IsNullOrWhiteSpace(AppConfig.DataDir)
            ? AppConfig.DataDir!
            : WinUiPaths.DefaultLocalDataDir();
        Directory.CreateDirectory(dir);
        return Path.Combine(dir, "tray.pid");
    }

    private static bool IsPidFileAlive()
    {
        try
        {
            var path = PidFilePath();
            if (!File.Exists(path)) return false;
            if (!int.TryParse(File.ReadAllText(path).Trim(), out var pid)) return false;
            try
            {
                var p = Process.GetProcessById(pid);
                return !p.HasExited;
            }
            catch
            {
                return false;
            }
        }
        catch
        {
            return false;
        }
    }

    public static bool TryStart()
    {
        lock (Gate)
        {
            if (_tray is { HasExited: false })
                return true;
            if (IsPidFileAlive())
                return true; // already running elsewhere

            // Prefer LocalAppData extract (Store) then package/sibling engine
            var root = BackendHost.ResolveBackendRoot();
            if (root is null || !BackendHost.LooksLikeEngine(root))
            {
                try
                {
                    root = EngineBootstrap.EnsureEngineAvailable(out _);
                }
                catch
                {
                    root = null;
                }
            }
            if (root is null)
                return false;

            // Same resolution as fiscal engine (embeddable python\ first — not system PATH)
            var py = BackendHost.ResolvePython(root);
            if (string.IsNullOrWhiteSpace(py) || py == "python" || !File.Exists(py))
                return false;

            try
            {
                var pyDir = Path.GetDirectoryName(py) ?? root;
                var workDir = BackendHost.IsRunnableEmbed(root) ? pyDir : root;
                var psi = new ProcessStartInfo
                {
                    FileName = py,
                    Arguments = "-m honestspend.cli tray",
                    WorkingDirectory = workDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                BackendHost.ApplyEmbedEnvironment(psi, root, pyDir);
                psi.Environment["FOS_HOST"] = "127.0.0.1";
                psi.Environment["FOS_PORT"] = "7420";
                if (!string.IsNullOrWhiteSpace(AppConfig.DataDir))
                    psi.Environment["FOS_DATA_DIR"] = AppConfig.DataDir.Trim();
                _tray = Process.Start(psi);
                return _tray is not null;
            }
            catch
            {
                return false;
            }
        }
    }

    public static void Stop()
    {
        lock (Gate)
        {
            try
            {
                if (_tray is { HasExited: false })
                {
                    _tray.Kill(entireProcessTree: true);
                    _tray.Dispose();
                }
            }
            catch
            {
                /* ignore */
            }
            finally
            {
                _tray = null;
            }

            // Best-effort: kill by pid file if we didn't own the process
            try
            {
                var path = PidFilePath();
                if (File.Exists(path) && int.TryParse(File.ReadAllText(path).Trim(), out var pid))
                {
                    try
                    {
                        var p = Process.GetProcessById(pid);
                        p.Kill(entireProcessTree: true);
                    }
                    catch
                    {
                        /* ignore */
                    }
                    File.Delete(path);
                }
            }
            catch
            {
                /* ignore */
            }
        }
    }
}
