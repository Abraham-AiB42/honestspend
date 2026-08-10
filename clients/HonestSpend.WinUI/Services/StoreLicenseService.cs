using System.Text.Json;
using Windows.Services.Store;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// Microsoft Store entitlement check (paid app / trial).
/// Syncs result into the local engine via POST /api/license/store.
/// </summary>
public static class StoreLicenseService
{
    public sealed record StoreCheck(
        bool Available,
        bool IsActive,
        bool IsTrial,
        string Detail,
        string? Sku = null);

    /// <summary>Query StoreContext for this app's license (packaged only).</summary>
    public static async Task<StoreCheck> CheckAppLicenseAsync()
    {
        if (!PackageInfo.IsPackaged)
        {
            return new StoreCheck(
                Available: false,
                IsActive: false,
                IsTrial: false,
                Detail: "Not a Microsoft Store package (dev / sideload build).");
        }

        try
        {
            var context = StoreContext.GetDefault();
            var license = await context.GetAppLicenseAsync();
            if (license is null)
            {
                return new StoreCheck(true, false, false, "Store returned no app license.");
            }

            // Paid full license: IsActive. Trial: IsActive + IsTrial.
            var active = license.IsActive;
            var trial = license.IsTrial;
            var detail = trial
                ? "Microsoft Store trial is active."
                : active
                    ? "Microsoft Store license is active."
                    : "No active Microsoft Store license for this account.";
            return new StoreCheck(true, active, trial, detail);
        }
        catch (Exception ex)
        {
            return new StoreCheck(
                Available: true,
                IsActive: false,
                IsTrial: false,
                Detail: "Store license check failed: " + ex.Message);
        }
    }

    /// <summary>Check Store + post entitlement to local engine.</summary>
    public static async Task<JsonElement?> SyncToEngineAsync(CancellationToken ct = default)
    {
        var check = await CheckAppLicenseAsync();
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync(ct);
            return await api.RegisterStoreLicenseAsync(
                isActive: check.IsActive,
                isTrial: check.IsTrial,
                detail: check.Detail,
                ct: ct);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Opens Store product page for purchase. Prefer product page when live;
    /// search fallback while listing is new.
    /// </summary>
    public static void OpenStoreListing()
    {
        try
        {
            var uri = new Uri("ms-windows-store://pdp/?PFN=" + GetPackageFamilyNameFallback());
            _ = Windows.System.Launcher.LaunchUriAsync(uri);
        }
        catch
        {
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "https://apps.microsoft.com/search?query=HonestSpend",
                    UseShellExecute = true,
                });
            }
            catch
            {
                /* ignore */
            }
        }
    }

    private static string GetPackageFamilyNameFallback()
    {
        try
        {
            return Windows.ApplicationModel.Package.Current?.Id?.FamilyName
                   ?? "AgencyinBox42.HonestSpend_fjke548ww9m4e";
        }
        catch
        {
            return "AgencyinBox42.HonestSpend_fjke548ww9m4e";
        }
    }
}
