using Windows.Storage;

namespace HonestSpend_WinUI.Services;

/// <summary>Apply data-folder choice, books-bundle copy, restart engine with FOS_DATA_DIR.</summary>
public static class StorageLocationService
{
    private static readonly string[] BundleNames =
    {
        "honestspend.db.sealed",
        "honestspend.db",
        "honestspend.db-wal",
        "honestspend.db-shm",
        "crypto.json",
        "license.json",
    };

    public static string DefaultLocalPath() => WinUiPaths.DefaultLocalDataDir();

    public static void PersistDataDir(string? path)
    {
        var trimmed = string.IsNullOrWhiteSpace(path) ? null : path.Trim();
        AppConfig.DataDir = trimmed;
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            ls["DataDir"] = trimmed ?? "";
        }
        catch { /* unpackaged */ }

        if (!string.IsNullOrWhiteSpace(trimmed))
        {
            try { Directory.CreateDirectory(trimmed); }
            catch { /* validated later */ }
        }
    }

    /// <summary>
    /// Copy books bundle (sealed first, then plaintext, crypto, license).
    /// Does not overwrite existing dest files.
    /// </summary>
    public static string CopyBooksBundle(string? fromDir, string toDir, bool includeSecrets = false)
    {
        fromDir = string.IsNullOrWhiteSpace(fromDir) ? DefaultLocalPath() : fromDir.Trim();
        toDir = toDir.Trim();
        if (string.Equals(
                Path.GetFullPath(fromDir).TrimEnd('\\', '/'),
                Path.GetFullPath(toDir).TrimEnd('\\', '/'),
                StringComparison.OrdinalIgnoreCase))
            return "Same folder — nothing to copy.";

        Directory.CreateDirectory(toDir);
        var copied = new List<string>();
        var skipped = new List<string>();
        var names = BundleNames.ToList();
        if (includeSecrets)
            names.Add("secrets.json");

        foreach (var name in names)
        {
            var src = Path.Combine(fromDir, name);
            var dest = Path.Combine(toDir, name);
            if (!File.Exists(src)) continue;
            if (File.Exists(dest))
            {
                skipped.Add(name);
                continue;
            }
            File.Copy(src, dest, overwrite: false);
            copied.Add(name);
        }

        if (copied.Count == 0 && skipped.Count == 0)
            return "No books files found at previous location — new folder is empty.";
        var msg = copied.Count > 0 ? "Copied: " + string.Join(", ", copied) : "Nothing new copied.";
        if (skipped.Count > 0)
            msg += " · Skipped (already at dest): " + string.Join(", ", skipped);
        return msg;
    }

    /// <summary>Legacy name used by setup wizard.</summary>
    public static string? CopyBooksIfNeeded(string? fromDir, string toDir, bool includeSecrets = false)
        => CopyBooksBundle(fromDir, toDir, includeSecrets);

    /// <summary>
    /// Checkpoint/seal source vault, copy full books bundle, set DataDir, restart engine.
    /// progress: optional UI status callback (called on the caller's context).
    /// </summary>
    public static async Task ApplyAndRestartEngineAsync(
        string path,
        bool copyFromPrevious = true,
        Action<string>? progress = null,
        CancellationToken ct = default)
    {
        void Report(string msg) => progress?.Invoke(msg);

        var previous = AppConfig.DataDir;
        if (string.IsNullOrWhiteSpace(previous))
            previous = DefaultLocalPath();

        // Seal while engine still points at the previous data dir (async — never GetResult)
        if (App.Backend is not null)
        {
            Report("Sealing books (if encrypted)…");
            try { await AppLockService.SealDatabaseAsync(ct).ConfigureAwait(false); }
            catch { /* may not be encrypted */ }
        }

        string? copyMsg = null;
        if (copyFromPrevious)
        {
            Report("Copying books to new folder…");
            // File I/O off UI thread
            var prev = previous;
            copyMsg = await Task.Run(() => CopyBooksBundle(prev, path), ct).ConfigureAwait(false);
        }

        Report("Saving folder setting…");
        PersistDataDir(path);

        if (App.Backend is not null)
        {
            Report("Restarting engine…");
            // Timeout so the wizard never freezes forever
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
            linked.CancelAfter(TimeSpan.FromSeconds(45));
            bool ok;
            try
            {
                ok = await App.Backend.RestartAsync(linked.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                throw new InvalidOperationException(
                    "Engine restart timed out after 45s. Close any other HonestSpend windows, " +
                    "then try again. " + (copyMsg ?? ""));
            }
            if (!ok)
                throw new InvalidOperationException(
                    "Data folder saved but engine did not restart: " +
                    (App.Backend.LastError ?? "unknown") +
                    (copyMsg is null ? "" : " · " + copyMsg));
        }

        Report("Engine ready.");
    }
}
