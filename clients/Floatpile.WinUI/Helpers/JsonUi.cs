using System.Globalization;
using System.Text.Json;

namespace Floatpile_WinUI.Helpers;

public static class JsonUi
{
    public static string Str(JsonElement el, string prop, string fallback = "—")
    {
        if (!el.TryGetProperty(prop, out var p) || p.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            return fallback;
        return p.ValueKind == JsonValueKind.String ? (p.GetString() ?? fallback) : p.GetRawText();
    }

    public static string Money(JsonElement el, string prop)
    {
        var s = Str(el, prop, "");
        if (decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var d))
            return d.ToString("C", CultureInfo.CurrentCulture);
        return string.IsNullOrEmpty(s) ? "—" : s;
    }

    public static int Int(JsonElement el, string prop, int fallback = 0)
    {
        if (!el.TryGetProperty(prop, out var p)) return fallback;
        if (p.ValueKind == JsonValueKind.Number && p.TryGetInt32(out var i)) return i;
        if (p.ValueKind == JsonValueKind.String && int.TryParse(p.GetString(), out var j)) return j;
        return fallback;
    }

    public static List<string> ArrayLines(JsonElement root, string prop, Func<JsonElement, string> map)
    {
        var list = new List<string>();
        if (!root.TryGetProperty(prop, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return list;
        foreach (var item in arr.EnumerateArray())
            list.Add(map(item));
        return list;
    }
}
