using Windows.Storage;

namespace HonestSpend_WinUI.Services;

/// <summary>Apply data-folder choice, books-bundle copy, restart engine with FOS_DATA_DIR.</summary>
public static class StorageLocationService
{
    private static readonly string[] BundleNames =
    {
        "financial_os.db.sealed",
        "crypto.json",
        "financial_os.db",
        "financial_os.db-wal",
        "financial_os.db-shm",
        "license.json",
    };

    public static string DefaultLocalPath()
        => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".financial-os");

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
    /// </summary>
    public static async Task ApplyAndRestartEngineAsync(
        string path,
        bool copyFromPrevious = true,
        CancellationToken ct = default)
    {
        var previous = AppConfig.DataDir;
        if (string.IsNullOrWhiteSpace(previous))
            previous = DefaultLocalPath();

        // Seal while engine still points at the previous data dir
        if (App.Backend is not null)
        {
            try { await AppLockService.SealDatabaseAsync(ct); }
            catch { /* may not be encrypted */ }
        }

        string? copyMsg = null;
        if (copyFromPrevious)
            copyMsg = CopyBooksBundle(previous, path);

        PersistDataDir(path);

        if (App.Backend is not null)
        {
            // RestartAsync seals again (idempotent) then kills with new FOS_DATA_DIR
            var ok = await App.Backend.RestartAsync(ct);
            if (!ok)
                throw new InvalidOperationException(
                    "Data folder saved but engine did not restart: " +
                    (App.Backend.LastError ?? "unknown") +
                    (copyMsg is null ? "" : " · " + copyMsg));
        }
    }
}
