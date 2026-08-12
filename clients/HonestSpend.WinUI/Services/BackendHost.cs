using System.Diagnostics;
using System.Text;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// Starts the Python HonestSpend API as a child process (Windows-first native host).
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
        if (await probe.HealthAsync(ct) && await DataDirMatchesAsync(probe, ct))
            return true;

        // Wrong data_dir peer on :7420 — kill our child if any and restart
        if (await probe.HealthAsync(ct) && !await DataDirMatchesAsync(probe, ct))
        {
            AppendLog("--- peer engine data_dir mismatch; restarting engine ---");
            Stop();
            await Task.Delay(400, ct);
        }

        // Store/MSIX: install engine from engine-portable.zip if needed
        try
        {
            var eng = EngineBootstrap.EnsureEngineAvailable(out var msg);
            if (eng is not null)
                AppendLog("--- " + msg + " ---");
            else if (!string.IsNullOrEmpty(msg))
                AppendLog("--- engine bootstrap: " + msg + " ---");
        }
        catch (Exception ex)
        {
            AppendLog("--- engine bootstrap error: " + ex.Message + " ---");
        }

        // Another client (or prior launch) may already be binding :7420.
        using (var recheck = new LedgerApiClient())
        {
            if (await recheck.HealthAsync(ct) && await DataDirMatchesAsync(recheck, ct))
                return true;
            if (await recheck.HealthAsync(ct) && !await DataDirMatchesAsync(recheck, ct))
            {
                // Cannot kill a peer we did not start — surface error
                LastError =
                    "Another HonestSpend engine is running with a different data folder. " +
                    "Close other instances, then retry.";
                AppendLog("--- " + LastError + " ---");
                return false;
            }
        }

        if (!TryStart())
        {
            // Race: peer won the port — accept if healthy and path matches.
            await Task.Delay(800, ct);
            using var peer = new LedgerApiClient();
            if (await peer.HealthAsync(ct) && await DataDirMatchesAsync(peer, ct))
                return true;
            return false;
        }

        for (var i = 0; i < 40; i++)
        {
            ct.ThrowIfCancellationRequested();
            await Task.Delay(500, ct);
            using var c = new LedgerApiClient();
            if (await c.HealthAsync(ct) && await DataDirMatchesAsync(c, ct))
                return true;
        }

        LastError = "Backend started but did not become healthy on :7420";
        return false;
    }

    /// <summary>
    /// True if engine data_dir matches AppConfig.DataDir.
    /// Fail-closed when DataDir is explicitly set but health lacks/mismatches path.
    /// </summary>
    private static async Task<bool> DataDirMatchesAsync(LedgerApiClient api, CancellationToken ct)
    {
        var wantRaw = string.IsNullOrWhiteSpace(AppConfig.DataDir)
            ? null
            : AppConfig.DataDir!.Trim();
        try
        {
            var h = await api.GetHealthDetailsAsync(ct);
            if (h is null)
            {
                // Explicit DataDir set but cannot read health → do not trust peer
                return wantRaw is null;
            }
            if (!h.Value.TryGetProperty("data_dir", out var dd) || dd.ValueKind != System.Text.Json.JsonValueKind.String)
            {
                return wantRaw is null; // old engine without field only OK for default
            }
            var engineDir = (dd.GetString() ?? "").Trim().TrimEnd('\\', '/');
            var want = wantRaw ?? WinUiPaths.DataDirRoot();
            want = Path.GetFullPath(want).TrimEnd('\\', '/');
            try { engineDir = Path.GetFullPath(engineDir).TrimEnd('\\', '/'); }
            catch { /* keep raw */ }
            if (string.IsNullOrEmpty(engineDir))
                return wantRaw is null;
            return string.Equals(engineDir, want, StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return wantRaw is null;
        }
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
                    "Could not find HonestSpend engine. Store builds need engine-portable.zip " +
                    "(auto-installs to %LocalAppData%\\HonestSpend\\engine). Zip installs need " +
                    "engine\\ next to the EXE. Or set Backend root in Settings.";
                return false;
            }

            var py = ResolvePython(root);
            OpenLog();

            var psi = new ProcessStartInfo
            {
                FileName = py,
                Arguments = "-m honestspend.cli serve --host 127.0.0.1 --port 7420",
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

            // Commercial license: Store/MSIX packages enforce; unpackaged stays OSS-unlocked
            // unless the user sets FOS_LICENSE_ENFORCE explicitly in the environment.
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("FOS_LICENSE_ENFORCE")))
            {
                psi.Environment["FOS_LICENSE_ENFORCE"] =
                    PackageInfo.ShouldEnforceLicense ? "1" : "0";
            }
            psi.Environment["FOS_LICENSE_DISTRIBUTION"] = PackageInfo.Distribution;

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
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".HonestSpend");
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
        // Prefer self-contained embeddable (Store / portable zip) over dev venv / system PATH
        foreach (var rel in new[]
                 {
                     Path.Combine("python", "python.exe"),
                     Path.Combine("python", "python"),
                     Path.Combine(".venv", "Scripts", "python.exe"),
                     Path.Combine(".venv", "Scripts", "python"),
                 })
        {
            var cand = Path.Combine(root, rel);
            if (File.Exists(cand))
                return cand;
        }
        // Last resort: system PATH (developers only — end-user packages must ship embeddable)
        return "python";
    }

    public static string? ResolveBackendRoot()
    {
        if (!string.IsNullOrWhiteSpace(AppConfig.BackendRoot) &&
            Directory.Exists(AppConfig.BackendRoot) &&
            LooksLikeEngine(AppConfig.BackendRoot))
            return AppConfig.BackendRoot;

        // Store install path (first-run extract of engine-portable.zip)
        var localStore = EngineBootstrap.LocalEngineRoot;
        if (LooksLikeEngine(localStore))
            return localStore;

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
        foreach (var repo in new[] { "honestspend", "financial-os", "HonestSpend" })
        {
            var known = Path.Combine(home, "source", "repos", repo);
            if (LooksLikeEngine(known))
                return known;
        }

        return null;
    }

    public static bool LooksLikeEngine(string root)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root))
            return false;
        // Self-contained embeddable (Store / GitHub zip)
        var embedPy = Path.Combine(root, "python", "python.exe");
        if (File.Exists(embedPy))
            return true;
        foreach (var pkg in new[] { "honestspend", "financial_os" })
        {
            var src = Path.Combine(root, "src", pkg);
            if (Directory.Exists(src))
                return true;
        }
        // Dev clone with venv
        var venvPy = Path.Combine(root, ".venv", "Scripts", "python.exe");
        return File.Exists(venvPy);
    }

    /// <summary>
    /// Kill the engine process. Does NOT seal — await SealDatabaseAsync first if needed.
    /// Sync seal via GetResult here deadlocks WinUI when Restart runs under a UI sync context.
    /// </summary>
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
        // Seal while engine is still alive (encrypted vaults) — async only
        try { await AppLockService.SealDatabaseAsync(ct).ConfigureAwait(false); }
        catch { /* best-effort */ }
        Stop();
        await Task.Delay(400, ct).ConfigureAwait(false);
        return await EnsureRunningAsync(ct).ConfigureAwait(false);
    }

    public void Dispose() => Stop();
}
