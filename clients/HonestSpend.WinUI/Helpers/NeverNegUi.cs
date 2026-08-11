using System.Text.Json;

namespace HonestSpend_WinUI.Helpers;

/// <summary>
/// Shared parse/soften for engine never-neg 409 (would_go_negative) on mark-paid paths.
/// confirm_required is true only when JSON sets it (warn mode). Hard mode: no dialog.
/// </summary>
public static class NeverNegUi
{
    /// <summary>
    /// Parse engine 409 never-neg payload. confirmRequired only when JSON sets true (warn).
    /// </summary>
    public static bool TryParseWouldGoNegative(
        Exception ex, out bool confirmRequired, out string message)
    {
        confirmRequired = false;
        message = "";
        var m = ex.Message ?? "";
        if (!m.StartsWith("409 ", StringComparison.Ordinal)
            && !m.Contains("would_go_negative", StringComparison.OrdinalIgnoreCase))
            return false;

        var brace = m.IndexOf('{');
        if (brace >= 0)
        {
            try
            {
                using var doc = JsonDocument.Parse(m[brace..]);
                var root = doc.RootElement;
                var detail = root;
                if (root.TryGetProperty("detail", out var d))
                    detail = d;
                if (detail.ValueKind == JsonValueKind.Object)
                {
                    var code = detail.TryGetProperty("code", out var c) ? c.GetString() : null;
                    if (string.Equals(code, "would_go_negative", StringComparison.OrdinalIgnoreCase)
                        || m.Contains("would_go_negative", StringComparison.OrdinalIgnoreCase))
                    {
                        message = detail.TryGetProperty("message", out var msgEl)
                            && msgEl.ValueKind == JsonValueKind.String
                            ? (msgEl.GetString() ?? "")
                            : "";
                        if (string.IsNullOrWhiteSpace(message))
                            message = "This would make checking negative.";
                        confirmRequired = detail.TryGetProperty("confirm_required", out var cr)
                            && cr.ValueKind == JsonValueKind.True;
                        return true;
                    }
                }
                else if (detail.ValueKind == JsonValueKind.String)
                {
                    var s = detail.GetString() ?? "";
                    if (s.Contains("would_go_negative", StringComparison.OrdinalIgnoreCase)
                        || s.Contains("negative", StringComparison.OrdinalIgnoreCase))
                    {
                        message = s;
                        confirmRequired = false;
                        return true;
                    }
                }
            }
            catch
            {
                /* fall through */
            }
        }

        if (m.Contains("would_go_negative", StringComparison.OrdinalIgnoreCase))
        {
            message = "This would make checking negative.";
            confirmRequired = false;
            return true;
        }

        return false;
    }

    /// <summary>Strip rescue API jargon from engine never-neg messages.</summary>
    public static string SoftenWouldGoNegativeMessage(string engineMessage)
    {
        var s = engineMessage.Trim();
        var rescue = s.IndexOf("Analyze rescue", StringComparison.OrdinalIgnoreCase);
        if (rescue > 0)
            s = s[..rescue].TrimEnd(' ', '.', ',');
        var post = s.IndexOf("POST /api/", StringComparison.OrdinalIgnoreCase);
        if (post > 0)
            s = s[..post].TrimEnd(' ', '.', ',');
        if (string.IsNullOrWhiteSpace(s))
            return "This would make checking negative.";
        return s.EndsWith('.') ? s : s + ".";
    }

    /// <summary>Friendly message for ErrorBar / dialog body after parse.</summary>
    public static string FriendlyMessage(string? engineMessage)
    {
        if (string.IsNullOrWhiteSpace(engineMessage))
            return "This would make checking negative.";
        return SoftenWouldGoNegativeMessage(engineMessage);
    }
}
