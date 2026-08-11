using Windows.Storage;

namespace HonestSpend_WinUI.Services;

/// <summary>Apply data-folder choice, optional DB copy, restart engine with FOS_DATA_DIR.</summary>
public static class StorageLocationService
{
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
    /// Copy financial_os.db (and sibling essentials) from old data dir to new if needed.
    /// </summary>
    public static string? CopyBooksIfNeeded(string? fromDir, string toDir)
    {
        fromDir = string.IsNullOrWhiteSpace(fromDir) ? DefaultLocalPath() : fromDir.Trim();
        toDir = toDir.Trim();
        if (string.Equals(
                Path.GetFullPath(fromDir).TrimEnd('\\', '/'),
                Path.GetFullPath(toDir).TrimEnd('\\', '/'),
                StringComparison.OrdinalIgnoreCase))
            return null;

        Directory.CreateDirectory(toDir);
        var srcDb = Path.Combine(fromDir, "financial_os.db");
        var destDb = Path.Combine(toDir, "financial_os.db");
        if (!File.Exists(srcDb))
            return "No existing books at previous location — new folder is empty.";
        if (File.Exists(destDb))
            return "Destination already has books — left as-is (no overwrite).";

        File.Copy(srcDb, destDb, overwrite: false);
        // Optional: secrets stay device-local by design; do not auto-copy secrets.json
        return $"Copied financial_os.db to {destDb}";
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
            var ok = await App.Backend.RestartAsync(ct);
            if (!ok)
                throw new InvalidOperationException(
                    "Data folder saved but engine did not restart: " +
                    (App.Backend.LastError ?? "unknown") +
                    (copyMsg is null ? "" : " · " + copyMsg));
        }
    }
}
