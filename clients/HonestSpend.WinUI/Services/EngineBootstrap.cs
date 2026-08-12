using System.IO.Compression;
using Windows.Storage;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// Resolves a runnable engine. Store packages use sibling engine\ only;
/// unpackaged zips may extract engine-portable.zip to LocalAppData.
/// </summary>
public static class EngineBootstrap
{
    public static string LocalEngineRoot =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "HonestSpend",
            "engine");

    /// <summary>
    /// Resolve or install engine. Returns engine root or null. Message is human status.
    /// </summary>
    public static string? EnsureEngineAvailable(out string message)
    {
        var existing = BackendHost.ResolveBackendRoot();
        if (existing is not null && BackendHost.IsAllowedEngineRoot(existing))
        {
            message = "Engine ready: " + existing;
            return existing;
        }

        var baseDir = AppContext.BaseDirectory;
        foreach (var rel in new[] { "engine", Path.Combine("..", "engine") })
        {
            try
            {
                var eng = Path.GetFullPath(Path.Combine(baseDir, rel));
                if (BackendHost.IsRunnableEmbed(eng) && BackendHost.IsAllowedEngineRoot(eng))
                {
                    PersistBackendRoot(eng);
                    message = "Engine found next to app: " + eng;
                    return eng;
                }
            }
            catch
            {
                /* ignore */
            }
        }

        if (PackageInfo.IsPackaged)
        {
            message =
                "This Store install is missing its private engine. Reinstall HonestSpend from the Store.";
            return null;
        }

        var zip = FindEnginePortableZip();
        if (zip is not null)
        {
            try
            {
                message = ExtractPortableZip(zip, LocalEngineRoot);
                if (BackendHost.IsRunnableEmbed(LocalEngineRoot))
                {
                    PersistBackendRoot(LocalEngineRoot);
                    return LocalEngineRoot;
                }
                message = "Extracted engine but python3xx.dll is missing: " + LocalEngineRoot;
                return null;
            }
            catch (Exception ex)
            {
                message = "Could not install engine from zip: " + ex.Message;
                return null;
            }
        }

        if (BackendHost.LooksLikeEngine(LocalEngineRoot))
        {
            PersistBackendRoot(LocalEngineRoot);
            message = "Engine ready (LocalAppData): " + LocalEngineRoot;
            return LocalEngineRoot;
        }

        message =
            "No engine found. Unpackaged zips need engine\\ next to the app, " +
            "or Settings → Backend root pointing at a clone with .venv.";
        return null;
    }

    public static string? FindEnginePortableZip()
    {
        var baseDir = AppContext.BaseDirectory;
        foreach (var name in new[]
                 {
                     "engine-portable.zip",
                     Path.Combine("Assets", "engine-portable.zip"),
                     Path.Combine("engine", "engine-portable.zip"),
                 })
        {
            var path = Path.Combine(baseDir, name);
            if (File.Exists(path))
                return path;
        }
        return null;
    }

    public static string ExtractPortableZip(string zipPath, string destRoot)
    {
        if (Directory.Exists(destRoot))
        {
            // Repair: only re-extract if python missing
            if (BackendHost.LooksLikeEngine(destRoot))
                return "Engine already installed at " + destRoot;
            try
            {
                Directory.Delete(destRoot, recursive: true);
            }
            catch
            {
                /* try extract over */
            }
        }

        var parent = Path.GetDirectoryName(destRoot);
        if (!string.IsNullOrEmpty(parent))
            Directory.CreateDirectory(parent);

        var temp = destRoot + ".extracting";
        if (Directory.Exists(temp))
            Directory.Delete(temp, recursive: true);
        Directory.CreateDirectory(temp);

        ZipFile.ExtractToDirectory(zipPath, temp, overwriteFiles: true);

        // Zip may contain a single top-level "engine" folder or flat layout
        var nested = Path.Combine(temp, "engine");
        var source = Directory.Exists(nested) && BackendHost.LooksLikeEngine(nested)
            ? nested
            : temp;

        if (Directory.Exists(destRoot))
            Directory.Delete(destRoot, recursive: true);
        Directory.Move(source, destRoot);

        // Clean temp if still around
        try
        {
            if (Directory.Exists(temp))
                Directory.Delete(temp, recursive: true);
        }
        catch
        {
            /* ignore */
        }

        return "Installed engine to " + destRoot;
    }

    public static void PersistBackendRoot(string root)
    {
        try
        {
            AppConfig.BackendRoot = root;
            var ls = ApplicationData.Current.LocalSettings.Values;
            ls["BackendRoot"] = root;
        }
        catch
        {
            AppConfig.BackendRoot = root;
        }
    }
}
