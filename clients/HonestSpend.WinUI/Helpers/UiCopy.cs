namespace HonestSpend_WinUI.Helpers;

/// <summary>Plain-language labels — never show IFPP/engine jargon on Simple path.</summary>
public static class UiCopy
{
    public const string SafeToSpend = "Safe to spend";
    public const string CanCharge = "Can charge (no interest)";
    public const string TotalPower = "Total power";
    public const string Who = "Who";
    public const string ThisMoney = "This money";
    public const string AllMoney = "All money";
    public const string DoThisNext = "Do this next";
    public const string StatusSafe = "Safe";
    public const string StatusWatch = "Watch";
    public const string StatusDanger = "Danger now";
    public const string AddSomething = "Add something";
    public const string FullBooks = "Full books";
    public const string SimpleMode = "Simple";
    public const string NextRisk = "Next risk day";
    public const string WealthDisclaimer = "Educational only — not investment, tax, or insurance advice.";
    public const string SpendableCash = "Count toward Safe to spend";

    /// <summary>Map engine autopay policy codes to human labels.</summary>
    public static string AutopayPolicy(string? policy) => (policy ?? "").ToLowerInvariant() switch
    {
        "min" => "Minimum only",
        "statement" => "Pay statement in full",
        "promo_sink" => "0% promo monthly set-aside",
        "none" or "" => "Nothing for now",
        _ => policy ?? "—",
    };

    public static string MoneyView(string? scope) =>
        string.Equals(scope, "group", StringComparison.OrdinalIgnoreCase) ? AllMoney : ThisMoney;

    public static string PayMethod(string? method) => (method ?? "").ToLowerInvariant() switch
    {
        "cash" => "Cash / checking",
        "card" or "credit" => "Card (interest-free when safe)",
        "auto" => "Best available",
        _ => string.IsNullOrEmpty(method) ? "—" : method,
    };

    public static string EntityType(string? entityType) => (entityType ?? "").ToLowerInvariant() switch
    {
        "personal" => "Personal",
        "business" => "Business",
        "child" => "Child",
        _ => string.IsNullOrEmpty(entityType) ? "Who" : entityType,
    };

    public static string AccountKind(string? kind) => (kind ?? "").ToLowerInvariant() switch
    {
        "checking" => "Checking",
        "savings" => "Savings",
        "credit" => "Credit card",
        "cash" => "Cash",
        "loan" => "Loan",
        "investment" => "Investment",
        _ => string.IsNullOrEmpty(kind) ? "Account" : kind,
    };
}
