using System.Globalization;

namespace HonestSpend_WinUI.Helpers;

/// <summary>
/// ISO date (yyyy-MM-dd) → plain English short weekday, e.g. "Fri Mar 6".
/// Unparseable values pass through unchanged.
/// </summary>
public static class PlainDateUi
{
    public static string FormatPlainWeekdayDate(string? isoOrDate)
    {
        if (string.IsNullOrWhiteSpace(isoOrDate))
            return isoOrDate ?? "";
        if (string.Equals(isoOrDate, "—", StringComparison.Ordinal))
            return isoOrDate;
        if (DateOnly.TryParse(isoOrDate, CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
            return d.ToString("ddd MMM d", CultureInfo.InvariantCulture);
        if (DateTime.TryParse(isoOrDate, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var dt))
            return dt.ToString("ddd MMM d", CultureInfo.InvariantCulture);
        return isoOrDate;
    }
}
