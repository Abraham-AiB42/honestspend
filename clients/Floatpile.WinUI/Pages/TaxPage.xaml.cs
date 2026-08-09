using System.Text.Json;
using Floatpile_WinUI.Helpers;
using Floatpile_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace Floatpile_WinUI.Pages;

public sealed partial class TaxPage : Page
{
    private JsonElement _profilesRaw = default;
    private bool _loading;

    public TaxPage()
    {
        InitializeComponent();
        YearBox.Value = DateTime.Now.Year;
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        _loading = true;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            _profilesRaw = await api.GetProfilesAsync();
            ProfileBox.Items.Clear();
            foreach (var p in _profilesRaw.EnumerateArray())
            {
                ProfileBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(p, "display_name")} ({JsonUi.Str(p, "slug")})",
                    Tag = p.GetProperty("id").GetInt32(),
                });
            }
            if (ProfileBox.Items.Count > 0) ProfileBox.SelectedIndex = 0;
            FillProfileFields();

            var coa = await api.GetTaxCoaSummaryAsync();
            CoaText.Text =
                $"{JsonUi.Str(coa, "total_categories")} categories · {JsonUi.Str(coa, "disclaimer")}";
            var forms = new List<string>();
            if (coa.TryGetProperty("by_tax_form", out var by) && by.ValueKind == JsonValueKind.Object)
            {
                foreach (var prop in by.EnumerateObject())
                    forms.Add($"{prop.Name}: {prop.Value.GetRawText()}");
            }
            CoaForms.ItemsSource = forms;
            MsgText.Text = "Ready.";
            await CheckReadyAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
        finally
        {
            _loading = false;
        }
    }

    private void Profile_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_loading) return;
        FillProfileFields();
    }

    private void FillProfileFields()
    {
        var pid = SelectedProfileId();
        if (pid is null || _profilesRaw.ValueKind != JsonValueKind.Array) return;
        foreach (var p in _profilesRaw.EnumerateArray())
        {
            if (p.GetProperty("id").GetInt32() != pid) continue;
            HomeStateBox.Text = JsonUi.Str(p, "home_state", "");
            MultiStateBox.IsChecked = p.TryGetProperty("multi_state", out var ms) && ms.GetBoolean();
            NotesBox.Text = JsonUi.Str(p, "filing_notes", "");
            AllocBox.Text = JsonUi.Str(p, "state_allocation_json", "");
            if (HomeStateBox.Text == "—") HomeStateBox.Text = "";
            if (NotesBox.Text == "—") NotesBox.Text = "";
            if (AllocBox.Text == "—") AllocBox.Text = "";
            break;
        }
    }

    private int? SelectedProfileId()
    {
        if (ProfileBox.SelectedItem is ComboBoxItem cbi && cbi.Tag is int id)
            return id;
        return null;
    }

    private int SelectedYear()
    {
        if (double.IsNaN(YearBox.Value)) return DateTime.Now.Year;
        return (int)YearBox.Value;
    }

    private async void SaveProfile_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick entity.");
            var body = new Dictionary<string, object?>
            {
                ["home_state"] = string.IsNullOrWhiteSpace(HomeStateBox.Text) ? null : HomeStateBox.Text.Trim(),
                ["multi_state"] = MultiStateBox.IsChecked == true,
                ["filing_notes"] = string.IsNullOrWhiteSpace(NotesBox.Text) ? null : NotesBox.Text.Trim(),
                ["state_allocation_json"] = string.IsNullOrWhiteSpace(AllocBox.Text) ? null : AllocBox.Text.Trim(),
            };
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.PatchProfileAsync(pid, body);
            MsgText.Text = "Entity tax fields saved.";
            _profilesRaw = await api.GetProfilesAsync();
            await CheckReadyAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Ready_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await CheckReadyAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task CheckReadyAsync(LedgerApiClient api)
    {
        var pid = SelectedProfileId();
        if (pid is null) return;
        var r = await api.GetTaxReadinessAsync(pid.Value, SelectedYear());
        var ready = r.TryGetProperty("ready", out var rd) && rd.GetBoolean();
        ReadyText.Text = ready
            ? $"READY for export · score {JsonUi.Str(r, "score")}"
            : $"NOT READY · score {JsonUi.Str(r, "score")}";
        var lines = new List<string>();
        if (r.TryGetProperty("issues", out var iss) && iss.ValueKind == JsonValueKind.Array)
        {
            foreach (var i in iss.EnumerateArray())
                lines.Add("✗ " + i.GetString());
        }
        if (r.TryGetProperty("warnings", out var w) && w.ValueKind == JsonValueKind.Array)
        {
            foreach (var i in w.EnumerateArray())
                lines.Add("! " + i.GetString());
        }
        if (lines.Count == 0) lines.Add("No issues.");
        ReadyIssues.ItemsSource = lines;
    }

    private async void Preview_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick an entity.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var packet = await api.GetTaxPacketAsync(pid, SelectedYear());
            RenderPacket(packet);
            MsgText.Text = "Preview loaded.";
            if (packet.TryGetProperty("readiness", out var r))
            {
                var ready = r.TryGetProperty("ready", out var rd) && rd.GetBoolean();
                ReadyText.Text = ready
                    ? $"READY · score {JsonUi.Str(r, "score")}"
                    : $"NOT READY · score {JsonUi.Str(r, "score")}";
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void RenderPacket(JsonElement packet)
    {
        var prof = packet.TryGetProperty("profile", out var pr) ? pr : default;
        var counts = packet.TryGetProperty("counts", out var c) ? c : default;
        PacketMeta.Text =
            $"{JsonUi.Str(prof, "display_name")} · {JsonUi.Str(prof, "entity_type")} · " +
            $"form {JsonUi.Str(prof, "tax_form_primary")} · state {JsonUi.Str(prof, "home_state", "—")} · " +
            $"multi {JsonUi.Str(prof, "multi_state")} · year {JsonUi.Str(packet, "year")} · " +
            $"txns {JsonUi.Str(counts, "transactions")} · lines {JsonUi.Str(counts, "tax_lines")} · " +
            $"uncat {JsonUi.Str(counts, "uncategorized")}";
        DisclaimerText.Text = JsonUi.Str(packet, "disclaimer");

        var lines = new List<LineRow>();
        if (packet.TryGetProperty("summary_by_tax_line", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var row in arr.EnumerateArray())
            {
                lines.Add(new LineRow(
                    $"{JsonUi.Str(row, "tax_form")} · {JsonUi.Str(row, "tax_line")} · {JsonUi.Str(row, "category_sample")}",
                    $"{JsonUi.Str(row, "txn_count")} txns · gross {JsonUi.Money(row, "gross_amount")} · " +
                    $"tax-relevant {JsonUi.Money(row, "tax_relevant_amount")} · {JsonUi.Str(row, "deductibility")}"));
            }
        }
        LineList.ItemsSource = lines;

        var uncat = new List<string>();
        if (packet.TryGetProperty("uncategorized_or_nontax", out var u) && u.ValueKind == JsonValueKind.Array)
        {
            foreach (var row in u.EnumerateArray().Take(25))
            {
                uncat.Add(
                    $"{JsonUi.Str(row, "date")} · {JsonUi.Str(row, "payee")} · {JsonUi.Money(row, "amount")} · " +
                    $"{JsonUi.Str(row, "category")}");
            }
            if (u.GetArrayLength() > 25)
                uncat.Add($"… and {u.GetArrayLength() - 25} more");
        }
        if (uncat.Count == 0) uncat.Add("None in this year (or all mapped).");
        UncatList.ItemsSource = uncat;
    }

    private async void Download_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick an entity.");
            var year = SelectedYear();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var bytes = await api.DownloadTaxPacketZipAsync(pid, year);
            await SaveZipAsync(bytes, $"tax_packet_{year}.zip");
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void CpaPack_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        TokenText.Text = "";
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick an entity.");
            var year = SelectedYear();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            // meta issues token once
            var meta = await api.CreateCpaPackMetaAsync(pid, year, issueToken: true);
            if (meta.TryGetProperty("token", out var tok) && tok.ValueKind == JsonValueKind.Object)
            {
                TokenText.Text =
                    $"CPA token (copy now):\n{JsonUi.Str(tok, "username")} · {JsonUi.Str(tok, "api_token")}\n" +
                    JsonUi.Str(tok, "hint");
            }
            if (meta.TryGetProperty("readiness", out var r))
            {
                var ready = r.TryGetProperty("ready", out var rd) && rd.GetBoolean();
                if (!ready)
                {
                    var cont = new ContentDialog
                    {
                        Title = "Packet not fully ready",
                        Content = "Export anyway? Issues may remain for the CPA.",
                        PrimaryButtonText = "Export",
                        CloseButtonText = "Cancel",
                        XamlRoot = XamlRoot,
                    };
                    if (await cont.ShowAsync() != ContentDialogResult.Primary) return;
                }
            }
            var bytes = await api.DownloadCpaPackAsync(pid, year, issueToken: false);
            await SaveZipAsync(bytes, JsonUi.Str(meta, "filename", $"cpa_pack_{year}.zip"));
            MsgText.Text = "CPA pack saved. Token also in ZIP README if issued.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Write_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var pid = SelectedProfileId() ?? throw new InvalidOperationException("Pick an entity.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.WriteTaxPacketAsync(pid, SelectedYear());
            MsgText.Text = $"Engine wrote packet → {JsonUi.Str(res, "path")}";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task SaveZipAsync(byte[] bytes, string suggestedName)
    {
        var picker = new FileSavePicker();
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.FileTypeChoices.Add("ZIP archive", new List<string> { ".zip" });
        picker.SuggestedFileName = Path.GetFileNameWithoutExtension(suggestedName);
        var window = App.MainWindowInstance
            ?? throw new InvalidOperationException("Main window not ready.");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(window));
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;
        await FileIO.WriteBytesAsync(file, bytes);
        MsgText.Text = $"Saved ZIP → {file.Path}";
    }

    private sealed record LineRow(string Title, string Subtitle);
}
