using System.Globalization;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace HonestSpend_WinUI.Helpers;

/// <summary>Books money: $1,234.56 default ink; negatives ($1.23) in red. Default until user prefs exist.</summary>
public static class MoneyUi
{
    public static readonly SolidColorBrush NegativeBrush = new(Color.FromArgb(255, 196, 43, 28));

    public static string Format(decimal amount)
    {
        var core = Math.Abs(amount).ToString("C", CultureInfo.CurrentCulture);
        return amount < 0 ? $"({core})" : core;
    }

    public static string FormatLoose(string? raw)
    {
        return TryParse(raw, out var d) ? Format(d) : (raw ?? "");
    }

    public static bool TryParse(string? raw, out decimal amount)
    {
        amount = 0;
        if (string.IsNullOrWhiteSpace(raw) || raw is "—" or "?")
            return false;
        var s = raw.Trim().Replace("(", "-").Replace(")", "").Replace("$", "").Replace(",", "");
        return decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out amount)
            || decimal.TryParse(s, NumberStyles.Any, CultureInfo.CurrentCulture, out amount);
    }

    public static TextBlock Block(decimal amount, double fontSize = 13)
    {
        var tb = new TextBlock
        {
            Text = Format(amount),
            FontSize = fontSize,
            IsTextSelectionEnabled = true,
        };
        if (amount < 0)
            tb.Foreground = NegativeBrush;
        return tb;
    }

    public static Run Run(decimal amount)
    {
        var run = new Run { Text = Format(amount) };
        if (amount < 0)
            run.Foreground = NegativeBrush;
        return run;
    }
}
