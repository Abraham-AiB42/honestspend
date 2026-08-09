namespace LedgerRing_WinUI.Services;

/// <summary>Shared UI selection for multi-entity Spendable scope + session mode.</summary>
public static class AppState
{
    /// <summary>null = use engine default profile for entity scope.</summary>
    public static int? SelectedProfileId { get; set; }

    /// <summary>entity (silo, default) or group (combined).</summary>
    public static string IfppScope { get; set; } = "entity";

    /// <summary>When true, hide write navigation (CPA / viewer session).</summary>
    public static bool ReadOnlySession { get; set; }

    /// <summary>Simple mode = north-star daily UI; FullBooks = cockpit.</summary>
    public static bool SimpleMode { get; set; } = true;

    /// <summary>Raised when shell entity/scope changes so pages can refresh.</summary>
    public static event Action? ScopeChanged;

    public static event Action? ModeChanged;

    public static void NotifyModeChanged() => ModeChanged?.Invoke();

    public static void NotifyScopeChanged() => ScopeChanged?.Invoke();

    public static string IfppQuery()
    {
        var q = $"scope={Uri.EscapeDataString(IfppScope)}";
        if (SelectedProfileId is int id && IfppScope == "entity")
            q += $"&profile_id={id}";
        return q;
    }
}
