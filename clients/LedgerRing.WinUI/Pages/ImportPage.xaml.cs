using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace LedgerRing_WinUI.Pages;

public sealed partial class ImportPage : Page
{
    private StorageFile? _csvFile;
    private StorageFile? _xlsxFile;

    public ImportPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            var accounts = await api.GetAccountsAsync();
            AccountBox.Items.Clear();
            foreach (var a in accounts.EnumerateArray())
            {
                AccountBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(a, "nickname")} [{JsonUi.Str(a, "kind")}] · #{a.GetProperty("id").GetInt32()}",
                    Tag = a.GetProperty("id").GetInt32(),
                });
            }
            if (AccountBox.Items.Count > 0) AccountBox.SelectedIndex = 0;

            var profiles = await api.GetProfilesAsync();
            ProfileSlugBox.Items.Clear();
            var idx = 0;
            var i = 0;
            foreach (var p in profiles.EnumerateArray())
            {
                var slug = JsonUi.Str(p, "slug");
                ProfileSlugBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(p, "display_name")} ({slug})",
                    Tag = slug,
                });
                if (slug == "personal") idx = i;
                i++;
            }
            if (ProfileSlugBox.Items.Count > 0)
                ProfileSlugBox.SelectedIndex = idx;

            await RefreshPlaidAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Plaid_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await RefreshPlaidAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task RefreshPlaidAsync(LedgerApiClient api)
    {
        var st = await api.GetPlaidStatusAsync();
        var enabled = st.TryGetProperty("enabled", out var en) && en.GetBoolean();
        var env = JsonUi.Str(st, "env", "?");
        var hint = JsonUi.Str(st, "hint");
        PlaidText.Text = enabled
            ? $"Plaid enabled · env {env}. Link flow still needs a browser step; CSV covers daily use."
            : $"Plaid off · {hint}";
    }

    private async void PickCsv_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".csv", ".txt" });
        if (file is null) return;
        _csvFile = file;
        CsvPathText.Text = file.Name;
    }

    private async void PickXlsx_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".xlsx" });
        if (file is null) return;
        _xlsxFile = file;
        XlsxPathText.Text = file.Name;
    }

    private async Task<StorageFile?> PickFileAsync(string[] extensions)
    {
        var picker = new FileOpenPicker();
        foreach (var ext in extensions)
            picker.FileTypeFilter.Add(ext);
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.ViewMode = PickerViewMode.List;

        var window = App.MainWindowInstance
            ?? throw new InvalidOperationException("Main window not ready.");
        var hwnd = WindowNative.GetWindowHandle(window);
        InitializeWithWindow.Initialize(picker, hwnd);
        return await picker.PickSingleFileAsync();
    }

    private async void PreviewCsv_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        PreviewText.Text = "";
        try
        {
            if (_csvFile is null) throw new InvalidOperationException("Pick a CSV first.");
            using var stream = await _csvFile.OpenStreamForReadAsync();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PreviewBankCsvAsync(stream, _csvFile.Name);
            var map = res.TryGetProperty("mapping", out var m) ? m : default;
            var lines = new List<string>
            {
                res.TryGetProperty("ok", out var ok) && ok.GetBoolean() ? "Mapping OK" : "Mapping issues",
                $"date → {JsonUi.Str(map, "date_col")} · payee → {JsonUi.Str(map, "description_col")} · " +
                $"amount → {JsonUi.Str(map, "amount_col")} · debit → {JsonUi.Str(map, "debit_col")} · credit → {JsonUi.Str(map, "credit_col")}",
            };
            if (res.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
            {
                foreach (var er in errs.EnumerateArray())
                    lines.Add("Error: " + er.GetString());
            }
            if (res.TryGetProperty("sample", out var sample) && sample.ValueKind == JsonValueKind.Array)
            {
                lines.Add("Sample:");
                foreach (var row in sample.EnumerateArray().Take(6))
                    lines.Add($"  {JsonUi.Str(row, "date")} · {JsonUi.Str(row, "payee")} · {JsonUi.Str(row, "amount")}");
            }
            lines.Add(JsonUi.Str(res, "hint"));
            PreviewText.Text = string.Join("\n", lines);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void ImportCsv_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        ResultText.Text = "";
        try
        {
            if (_csvFile is null) throw new InvalidOperationException("Pick a CSV first.");
            if (AccountBox.SelectedItem is not ComboBoxItem ai || ai.Tag is not int accountId)
                throw new InvalidOperationException("Pick a target account.");
            var sign = "bank";
            if (SignBox.SelectedItem is ComboBoxItem si && si.Tag is string st)
                sign = st;

            using var stream = await _csvFile.OpenStreamForReadAsync();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ImportBankCsvAsync(
                stream,
                _csvFile.Name,
                accountId,
                sign,
                AutoCatBox.IsChecked == true);
            ResultText.Text =
                $"CSV done · scanned {Prop(res, "rows_scanned")} · created {Prop(res, "transactions_created")} · " +
                $"skipped existing {Prop(res, "skipped_existing")} · bad {Prop(res, "skipped_bad")} · " +
                $"categorized {Prop(res, "categorized")}";
            if (res.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
            {
                var list = errs.EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrEmpty(x)).Take(8);
                var joined = string.Join("; ", list!);
                if (!string.IsNullOrEmpty(joined))
                    ResultText.Text += "\nErrors: " + joined;
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void ImportXlsx_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        ResultText.Text = "";
        try
        {
            if (_xlsxFile is null) throw new InvalidOperationException("Pick an xlsx first.");
            var slug = "personal";
            if (ProfileSlugBox.SelectedItem is ComboBoxItem pi && pi.Tag is string s)
                slug = s;

            using var stream = await _xlsxFile.OpenStreamForReadAsync();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ImportBudgetXlsxAsync(
                stream,
                _xlsxFile.Name,
                slug,
                DryRunBox.IsChecked == true);
            ResultText.Text =
                $"XLSX · scanned {Prop(res, "rows_scanned")} · created {Prop(res, "transactions_created")} · " +
                $"skipped empty {Prop(res, "skipped_empty")} · existing {Prop(res, "skipped_existing")} · " +
                $"range {JsonUi.Str(res, "date_from", "?")} → {JsonUi.Str(res, "date_to", "?")} · " +
                $"dry_run={Prop(res, "dry_run")}";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static string Prop(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var p)) return "?";
        return p.ValueKind switch
        {
            JsonValueKind.String => p.GetString() ?? "?",
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => "null",
            _ => p.GetRawText(),
        };
    }
}
