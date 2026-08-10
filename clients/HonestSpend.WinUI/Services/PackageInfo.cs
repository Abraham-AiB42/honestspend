namespace HonestSpend_WinUI.Services;

/// <summary>Packaging / distribution detection for license enforcement.</summary>
public static class PackageInfo
{
    private static readonly Lazy<bool> Packaged = new(DetectPackaged);

    /// <summary>True when running as MSIX / Store package (Desktop Bridge).</summary>
    public static bool IsPackaged => Packaged.Value;

    /// <summary>
    /// Store builds enforce commercial license (unless override env is set).
    /// Unpackaged dev / zip installs stay OSS-unlocked by default.
    /// </summary>
    public static bool ShouldEnforceLicense
    {
        get
        {
            var env = Environment.GetEnvironmentVariable("FOS_LICENSE_ENFORCE");
            if (!string.IsNullOrWhiteSpace(env))
            {
                return env is "1" or "true" or "TRUE" or "yes" or "YES";
            }
            return IsPackaged;
        }
    }

    public static string Distribution => IsPackaged ? "store" : "unpackaged";

    private static bool DetectPackaged()
    {
        try
        {
            var package = Windows.ApplicationModel.Package.Current;
            // Identity name empty when not packaged
            return package is not null && !string.IsNullOrWhiteSpace(package.Id?.Name);
        }
        catch
        {
            return false;
        }
    }
}
