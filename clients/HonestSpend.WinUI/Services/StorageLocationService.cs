using Windows.Storage;

namespace HonestSpend_WinUI.Services;

/// <summary>Apply data-folder choice, optional books-bundle copy, restart engine with FOS_DATA_DIR.</summary>
public static class StorageLocationService
{
    private static readonly string[] BundleNames =
    {
        "financial_os.db",
        "financial_os.db-wal",
        "financial_os.db-shm",
        "financial_os.db.sealed",
        "crypto.json",
        "license.json",
        // secrets.json intentionally opt-in / device-local by default
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
    /// Copy books bundle (DB, sealed, crypto meta, license) from old data dir to new.
    /// Does not overwrite existing dest files.
    /// </summary>
    public static string? CopyBooksIfNeeded(string? fromDir, string toDir, bool includeSecrets = false)
    {
        fromDir = string.IsNullOrWhiteSpace(fromDir) ? DefaultLocalPath() : fromDir.Trim();
        toDir = toDir.Trim();
        if (string.Equals(
                Path.GetFullPath(fromDir).TrimEnd('\\', '/'),
                Path.GetFullPath(toDir).TrimEnd('\\', '/'),
                StringComparison.OrdinalIgnoreCase))
            return null;

        Directory.CreateDirectory(toDir);
        var copied = new List<string>();
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
                copied.Add($"{name} (skipped — exists)");
                continue;
            }
            File.Copy(src, dest, overwrite: false);
            copied.Add(name);
        }

        if (copied.Count == 0)
            return "No books files found at previous location — new folder is empty.";
        return "Copied: " + string.Join(", ", copied);
    }

    public static async Task ApplyAndRestartEngineAsync(
        string path,
        bool copyFromPrevious = true,
        CancellationToken ct = default)
    {
        var previous = AppConfig.DataDir;
        if (string.IsNullOrWhiteSpace(previous))
            previous = DefaultLocalPath();

        PersistDataDir(path);
        string? copyMsg = null;
        if (copyFromPrevious)
            copyMsg = CopyBooksIfNeeded(previous, path);

        if (App.Backend is not null)
        {
            // Seal first if possible so we don't leave plaintext mid-move
            try { await AppLockService.SealDatabaseAsync(ct); } catch { /* optional */ }
            var ok = await App.Backend.RestartAsync(ct);
            if (!ok)
                throw new InvalidOperationException(
                    "Data folder saved but engine did not restart: " +
                    (App.Backend.LastError ?? "unknown") +
                    (copyMsg is null ? "" : " · " + copyMsg));
        }
    }
}
