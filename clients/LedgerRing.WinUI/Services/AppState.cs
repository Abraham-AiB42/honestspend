namespace LedgerRing_WinUI.Services;

/// <summary>Shared UI selection for multi-entity Spendable scope.</summary>
public static class AppState
{
    /// <summary>null = use engine default profile for entity scope.</summary>
    public static int? SelectedProfileId { get; set; }

    /// <summary>entity (silo, default) or group (combined).</summary>
    public static string IfppScope { get; set; } = "entity";

    public static string IfppQuery()
    {
        var q = $"scope={Uri.EscapeDataString(IfppScope)}";
        if (SelectedProfileId is int id && IfppScope == "entity")
            q += $"&profile_id={id}";
        return q;
    }
}
