namespace LedgerRing_WinUI.Services;

/// <summary>Windows-first client config. Backend stays Python/local API.</summary>
public static class AppConfig
{
    public static string BaseUrl { get; set; } = "http://127.0.0.1:7420";

    /// <summary>Optional X-API-Key for multi-user.</summary>
    public static string? ApiKey { get; set; }

    /// <summary>
    /// Repo root or install dir containing .venv and src.
    /// Default: walk up from app folder looking for pyproject.toml / financial-os.
    /// </summary>
    public static string? BackendRoot { get; set; }

    /// <summary>Passed to engine as FOS_DATA_DIR (OneDrive-friendly relocate).</summary>
    public static string? DataDir { get; set; }

    /// <summary>Launch pystray process when the WinUI app starts (after engine is up).</summary>
    public static bool StartTrayWithApp { get; set; }

    /// <summary>Start with the main window minimized.</summary>
    public static bool StartMinimized { get; set; }

    /// <summary>CLI / logon: hide UI, run engine + tray only.</summary>
    public static bool TrayOnly { get; set; }

    /// <summary>Optional deep-link page tag from CLI (review, reports, settings, …).</summary>
    public static string? OpenPage { get; set; }

    /// <summary>Parse Environment.GetCommandLineArgs() into flags.</summary>
    public static void ApplyCommandLine(string[] args)
    {
        var list = args.Skip(1).ToList();
        for (var i = 0; i < list.Count; i++)
        {
            var a = list[i];
            if (a.Equals("--tray-only", StringComparison.OrdinalIgnoreCase) ||
                a.Equals("/tray-only", StringComparison.OrdinalIgnoreCase))
            {
                TrayOnly = true;
                StartTrayWithApp = true;
                StartMinimized = true;
            }
            else if (a.Equals("--minimized", StringComparison.OrdinalIgnoreCase) ||
                     a.Equals("/minimized", StringComparison.OrdinalIgnoreCase))
            {
                StartMinimized = true;
            }
            else if (a.Equals("--tray", StringComparison.OrdinalIgnoreCase))
            {
                StartTrayWithApp = true;
            }
            else if (a.StartsWith("--page=", StringComparison.OrdinalIgnoreCase) ||
                     a.StartsWith("--open=", StringComparison.OrdinalIgnoreCase) ||
                     a.StartsWith("/page=", StringComparison.OrdinalIgnoreCase))
            {
                OpenPage = a.Split('=', 2)[1].Trim().Trim('"');
            }
            else if ((a.Equals("--page", StringComparison.OrdinalIgnoreCase) ||
                      a.Equals("--open", StringComparison.OrdinalIgnoreCase) ||
                      a.Equals("/page", StringComparison.OrdinalIgnoreCase))
                     && i + 1 < list.Count)
            {
                OpenPage = list[++i].Trim().Trim('"');
            }
        }

        if (!string.IsNullOrWhiteSpace(OpenPage))
            WinUiPaths.WriteNavigateRequest(OpenPage);
    }
}
