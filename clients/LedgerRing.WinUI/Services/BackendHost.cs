using System.Diagnostics;
using System.Text;

namespace LedgerRing_WinUI.Services;

/// <summary>
/// Starts the Python LedgerRing API as a child process (Windows-first native host).
/// Not a WebView wrapper — WinUI is the UI; Python remains the engine.
/// </summary>
public sealed class BackendHost : IDisposable
{
    private Process? _process;
    private StreamWriter? _logWriter;

    public bool IsRunning => _process is { HasExited: false };

    public string? LastError { get; private set; }

    public string? ResolvedRoot { get; private set; }

    public string? LogPath { get; private set; }

    public async Task<bool> EnsureRunningAsync(CancellationToken ct = default)
    {
        using var probe = new LedgerApiClient();
        if (await probe.HealthAsync(ct))
            return true;

        if (!TryStart())
            return false;

        for (var i = 0; i < 40; i++)
        {
            ct.ThrowIfCancellationRequested();
            await Task.Delay(500, ct);
            using var c = new LedgerApiClient();
            if (await c.HealthAsync(ct))
                return true;
        }

        LastError = "Backend started but did not become healthy on :7420";
        return false;
    }

    public bool TryStart()
    {
        try
        {
            var root = ResolveBackendRoot();
            ResolvedRoot = root;
            if (root is null)
            {
                LastError =
                    "Could not find LedgerRing engine. Place a repo clone, or an `engine\\` folder " +
                    "next to the EXE (with .venv + src), or set Backend root in Settings.";
                return false;
            }

            var py = ResolvePython(root);
            OpenLog();

            var psi = new ProcessStartInfo
            {
                FileName = py,
                Arguments = "-m financial_os.cli serve --host 127.0.0.1 --port 7420",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            psi.Environment["FOS_HOST"] = "127.0.0.1";
            psi.Environment["FOS_PORT"] = "7420";
            if (!string.IsNullOrWhiteSpace(AppConfig.DataDir))
                psi.Environment["FOS_DATA_DIR"] = AppConfig.DataDir.Trim();

            _process = Process.Start(psi);
            if (_process is null)
            {
                LastError = "Failed to start python process.";
                return false;
            }

            _process.OutputDataReceived += (_, e) => AppendLog(e.Data);
            _process.ErrorDataReceived += (_, e) => AppendLog(e.Data);
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();
            AppendLog($"--- engine start pid={_process.Id} root={root} ---");
            return true;
        }
        catch (Exception ex)
        {
            LastError = ex.Message;
            return false;
        }
    }

    private void OpenLog()
    {
        try
        {
            var dir = !string.IsNullOrWhiteSpace(AppConfig.DataDir)
                ? AppConfig.DataDir!
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".financial-os");
            Directory.CreateDirectory(dir);
            LogPath = Path.Combine(dir, "engine.log");
            _logWriter?.Dispose();
            _logWriter = new StreamWriter(new FileStream(LogPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
            {
                AutoFlush = true,
            };
        }
        catch
        {
            LogPath = null;
            _logWriter = null;
        }
    }

    private void AppendLog(string? line)
    {
        if (string.IsNullOrEmpty(line) || _logWriter is null) return;
        try
        {
            _logWriter.WriteLine($"{DateTime.Now:O} {line}");
        }
        catch
        {
            /* ignore */
        }
    }

    public static string ResolvePython(string root)
    {
        foreach (var rel in new[]
                 {
                     Path.Combine(".venv", "Scripts", "python.exe"),
                     Path.Combine(".venv", "Scripts", "python"),
                     Path.Combine("python", "python.exe"),
                     Path.Combine("python", "python"),
                 })
        {
            var cand = Path.Combine(root, rel);
            if (File.Exists(cand))
                return cand;
        }
        return "python";
    }

    public static string? ResolveBackendRoot()
    {
        if (!string.IsNullOrWhiteSpace(AppConfig.BackendRoot) &&
            Directory.Exists(AppConfig.BackendRoot) &&
            LooksLikeEngine(AppConfig.BackendRoot))
            return AppConfig.BackendRoot;

        var baseDir = new DirectoryInfo(AppContext.BaseDirectory);

        foreach (var engineRel in new[] { "engine", Path.Combine("..", "engine") })
        {
            var eng = Path.GetFullPath(Path.Combine(baseDir.FullName, engineRel));
            if (LooksLikeEngine(eng))
                return eng;
        }

        var dir = baseDir;
        for (var i = 0; i < 12 && dir is not null; i++, dir = dir.Parent)
        {
            if (LooksLikeEngine(dir.FullName))
                return dir.FullName;
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var known = Path.Combine(home, "source", "repos", "financial-os");
        if (LooksLikeEngine(known))
            return known;

        return null;
    }

    public static bool LooksLikeEngine(string root)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root))
            return false;
        var src = Path.Combine(root, "src", "financial_os");
        if (Directory.Exists(src))
            return true;
        var venvPy = Path.Combine(root, ".venv", "Scripts", "python.exe");
        return File.Exists(venvPy);
    }

    public void Stop()
    {
        try
        {
            if (_process is { HasExited: false })
            {
                AppendLog($"--- engine stop pid={_process.Id} ---");
                _process.Kill(entireProcessTree: true);
                _process.Dispose();
            }
        }
        catch
        {
            /* ignore */
        }
        finally
        {
            _process = null;
            try { _logWriter?.Dispose(); } catch { /* ignore */ }
            _logWriter = null;
        }
    }

    public async Task<bool> RestartAsync(CancellationToken ct = default)
    {
        Stop();
        await Task.Delay(400, ct);
        return await EnsureRunningAsync(ct);
    }

    public void Dispose() => Stop();
}
