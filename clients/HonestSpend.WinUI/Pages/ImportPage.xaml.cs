using System.Diagnostics;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace HonestSpend_WinUI.Pages;

public sealed partial class ImportPage : Page
{
    private StorageFile? _csvFile;
    private StorageFile? _ofxFile;
    private StorageFile? _pdfFile;
    private StorageFile? _xlsxFile;
    private string? _inboxPath;

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
                    Content = $"{JsonUi.Str(a, "nickname")} · {UiCopy.AccountKind(JsonUi.Str(a, "kind"))}",
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
                    Content = $"{JsonUi.Str(p, "display_name")} · {UiCopy.EntityType(JsonUi.Str(p, "entity_type"))}",
                    Tag = slug,
                });
                if (slug == "personal") idx = i;
                i++;
            }
            if (ProfileSlugBox.Items.Count > 0)
                ProfileSlugBox.SelectedIndex = idx;

            await LoadBankGuidesAsync(api);
            await RefreshInboxAsync(api);
            await RefreshPlaidAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task LoadBankGuidesAsync(LedgerApiClient api)
    {
        var guides = await api.GetBankGuidesAsync();
        BankGuideBox.Items.Clear();
        if (guides.TryGetProperty("guides", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var g in arr.EnumerateArray())
            {
                BankGuideBox.Items.Add(new ComboBoxItem
                {
                    Content = JsonUi.Str(g, "name"),
                    Tag = g.Clone(),
                });
            }
        }
        if (BankGuideBox.Items.Count > 0)
            BankGuideBox.SelectedIndex = 0;
    }

    private void BankGuide_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (BankGuideBox.SelectedItem is not ComboBoxItem { Tag: JsonElement g })
        {
            BankStepsText.Text = "";
            BankNotesText.Text = "";
            BankLoginLink.Visibility = Visibility.Collapsed;
            return;
        }
        var steps = new List<string>();
        if (g.TryGetProperty("steps", out var st) && st.ValueKind == JsonValueKind.Array)
        {
            var n = 1;
            foreach (var s in st.EnumerateArray())
            {
                steps.Add($"{n}. {s.GetString()}");
                n++;
            }
        }
        BankStepsText.Text = string.Join("\n", steps);
        BankNotesText.Text = JsonUi.Str(g, "notes", "");
        var url = JsonUi.Str(g, "login_url", "");
        if (!string.IsNullOrWhiteSpace(url) && Uri.TryCreate(url, UriKind.Absolute, out var uri))
        {
            BankLoginLink.NavigateUri = uri;
            BankLoginLink.Content = "Open " + JsonUi.Str(g, "name") + " login";
            BankLoginLink.Visibility = Visibility.Visible;
        }
        else
        {
            BankLoginLink.Visibility = Visibility.Collapsed;
        }
    }

    private async Task RefreshInboxAsync(LedgerApiClient api)
    {
        var inbox = await api.GetImportInboxAsync();
        _inboxPath = JsonUi.Str(inbox, "inbox", "");
        InboxPathText.Text = string.IsNullOrEmpty(_inboxPath)
            ? "Inbox path unavailable"
            : "Folder: " + _inboxPath;
        var count = 0;
        var names = new List<string>();
        if (inbox.TryGetProperty("files", out var files) && files.ValueKind == JsonValueKind.Array)
        {
            foreach (var f in files.EnumerateArray())
            {
                count++;
                names.Add(JsonUi.Str(f, "name"));
            }
        }
        InboxFilesText.Text = count == 0
            ? "No CSV files waiting."
            : $"{count} file(s): " + string.Join(", ", names.Take(8));
    }

    private async void RefreshInbox_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await RefreshInboxAsync(api);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void OpenInbox_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var path = _inboxPath;
            if (string.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
                throw new InvalidOperationException("Inbox folder not ready — start the engine and Refresh.");
            Process.Start(new ProcessStartInfo
            {
                FileName = "explorer.exe",
                Arguments = $"\"{path}\"",
                UseShellExecute = true,
            });
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void ImportInbox_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        ResultText.Text = "";
        try
        {
            int? defaultAcct = null;
            if (AccountBox.SelectedItem is ComboBoxItem { Tag: int id })
                defaultAcct = id;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ProcessImportInboxAsync(
                defaultAccountId: defaultAcct,
                autoCategorize: AutoCatBox.IsChecked == true);
            var created = JsonUi.Str(res, "transactions_created", "0");
            var seen = JsonUi.Str(res, "files_seen", "0");
            ResultText.Text = $"Inbox · {seen} file(s) · {created} transactions created.";
            if (res.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in results.EnumerateArray().Take(12))
                {
                    var line =
                        $"{JsonUi.Str(r, "file")} → {JsonUi.Str(r, "account_nickname", "?")} · " +
                        $"+{JsonUi.Str(r, "transactions_created", "0")}";
                    if (r.TryGetProperty("error", out var er) && er.ValueKind == JsonValueKind.String)
                        line += " · " + er.GetString();
                    ResultText.Text += "\n" + line;
                }
            }
            await RefreshInboxAsync(api);
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
        _ofxFile = null;
        _pdfFile = null;
        CsvPathText.Text = file.Name;
    }

    private async void PickOfx_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".ofx", ".qfx" });
        if (file is null) return;
        _ofxFile = file;
        _csvFile = null;
        _pdfFile = null;
        CsvPathText.Text = file.Name + " (OFX/QFX)";
    }

    private async void PickPdf_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".pdf" });
        if (file is null) return;
        _pdfFile = file;
        _csvFile = null;
        _ofxFile = null;
        CsvPathText.Text = file.Name + " (PDF)";
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
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            if (_pdfFile is not null)
            {
                using var stream = await _pdfFile.OpenStreamForReadAsync();
                var pdfRes = await api.PreviewStatementPdfAsync(stream, _pdfFile.Name);
                var pdfLines = new List<string>
                {
                    $"PDF · pages {JsonUi.Str(pdfRes, "pages")} · candidates {JsonUi.Str(pdfRes, "candidates")}",
                    JsonUi.Str(pdfRes, "hint"),
                };
                if (pdfRes.TryGetProperty("sample", out var pdfSample) && pdfSample.ValueKind == JsonValueKind.Array)
                {
                    pdfLines.Add("Sample:");
                    foreach (var row in pdfSample.EnumerateArray().Take(8))
                        pdfLines.Add($"  {JsonUi.Str(row, "txn_date")} · {JsonUi.Str(row, "payee")} · {JsonUi.Str(row, "amount")}");
                }
                PreviewText.Text = string.Join("\n", pdfLines);
                return;
            }
            if (_ofxFile is not null)
            {
                using var ofxStream = await _ofxFile.OpenStreamForReadAsync();
                var ofxRes = await api.PreviewOfxAsync(ofxStream, _ofxFile.Name);
                var ofxLines = new List<string>
                {
                    $"OFX/QFX · {JsonUi.Str(ofxRes, "transactions_found")} transactions" +
                    (string.IsNullOrEmpty(JsonUi.Str(ofxRes, "account_hint")) ? "" : $" · acct {JsonUi.Str(ofxRes, "account_hint")}") +
                    (string.IsNullOrEmpty(JsonUi.Str(ofxRes, "ledger_balance")) ? "" : $" · ledger ${JsonUi.Str(ofxRes, "ledger_balance")}"),
                    JsonUi.Str(ofxRes, "hint"),
                };
                if (ofxRes.TryGetProperty("sample", out var ofxSample) && ofxSample.ValueKind == JsonValueKind.Array)
                {
                    ofxLines.Add("Sample:");
                    foreach (var row in ofxSample.EnumerateArray().Take(8))
                        ofxLines.Add($"  {JsonUi.Str(row, "txn_date")} · {JsonUi.Str(row, "payee")} · {JsonUi.Str(row, "amount")}");
                }
                PreviewText.Text = string.Join("\n", ofxLines);
                return;
            }
            if (_csvFile is null) throw new InvalidOperationException("Pick a CSV, OFX/QFX, or PDF first.");
            using var streamCsv = await _csvFile.OpenStreamForReadAsync();
            var csvRes = await api.PreviewBankCsvAsync(streamCsv, _csvFile.Name);
            var map = csvRes.TryGetProperty("mapping", out var m) ? m : default;
            var csvLines = new List<string>
            {
                csvRes.TryGetProperty("ok", out var ok) && ok.GetBoolean() ? "Mapping OK" : "Mapping issues",
                $"date → {JsonUi.Str(map, "date_col")} · payee → {JsonUi.Str(map, "description_col")} · " +
                $"amount → {JsonUi.Str(map, "amount_col")} · debit → {JsonUi.Str(map, "debit_col")} · credit → {JsonUi.Str(map, "credit_col")}",
            };
            if (csvRes.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
            {
                foreach (var er in errs.EnumerateArray())
                    csvLines.Add("Error: " + er.GetString());
            }
            if (csvRes.TryGetProperty("sample", out var csvSample) && csvSample.ValueKind == JsonValueKind.Array)
            {
                csvLines.Add("Sample:");
                foreach (var row in csvSample.EnumerateArray().Take(6))
                    csvLines.Add($"  {JsonUi.Str(row, "date")} · {JsonUi.Str(row, "payee")} · {JsonUi.Str(row, "amount")}");
            }
            csvLines.Add(JsonUi.Str(csvRes, "hint"));
            PreviewText.Text = string.Join("\n", csvLines);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void HideNextSteps()
    {
        NextStepsPanel.Visibility = Visibility.Collapsed;
        GoSortBtn.Visibility = Visibility.Collapsed;
        GoReconcileBtn.Visibility = Visibility.Collapsed;
        GoHomeBtn.Visibility = Visibility.Collapsed;
    }

    private void ShowNextSteps(JsonElement res)
    {
        HideNextSteps();
        if (!res.TryGetProperty("next_steps", out var steps) || steps.ValueKind != JsonValueKind.Array)
            return;
        var show = false;
        foreach (var st in steps.EnumerateArray())
        {
            var action = JsonUi.Str(st, "action");
            if (action == "review")
            {
                GoSortBtn.Visibility = Visibility.Visible;
                show = true;
            }
            else if (action == "reconcile")
            {
                GoReconcileBtn.Visibility = Visibility.Visible;
                show = true;
            }
            else if (action is "home" or "hold")
            {
                GoHomeBtn.Visibility = Visibility.Visible;
                show = true;
            }
        }
        NextStepsPanel.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
    }

    private void GoSort_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("review");
    }

    private void GoReconcile_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("reconcile");
    }

    private void GoHome_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("home");
    }

    private async void ImportCsv_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        ResultText.Text = "";
        HideNextSteps();
        try
        {
            if (AccountBox.SelectedItem is not ComboBoxItem ai || ai.Tag is not int accountId)
                throw new InvalidOperationException("Pick a target account.");
            var sign = "bank";
            if (SignBox.SelectedItem is ComboBoxItem si && si.Tag is string st)
                sign = st;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            if (_ofxFile is not null)
            {
                using var stream = await _ofxFile.OpenStreamForReadAsync();
                var ofxRes = await api.ImportOfxAsync(
                    stream, _ofxFile.Name, accountId, sign, AutoCatBox.IsChecked == true);
                var lines = new List<string>
                {
                    $"OFX/QFX done · found {Prop(ofxRes, "transactions_found")} · created {Prop(ofxRes, "transactions_created")} · " +
                    $"skipped {Prop(ofxRes, "skipped_existing")} · categorized {Prop(ofxRes, "categorized")}",
                };
                var ledger = Prop(ofxRes, "ledger_balance");
                if (!string.IsNullOrEmpty(ledger))
                {
                    lines.Add(
                        $"Bank ledger bal ${ledger}" +
                        (string.IsNullOrEmpty(Prop(ofxRes, "drift")) ? "" : $" · books drift ${Prop(ofxRes, "drift")}") +
                        (ofxRes.TryGetProperty("institution_balance_set", out var ibs) && ibs.ValueKind == JsonValueKind.True
                            ? " · set for Reconcile"
                            : ""));
                }
                if (ofxRes.TryGetProperty("next_steps", out var steps) && steps.ValueKind == JsonValueKind.Array)
                {
                    foreach (var step in steps.EnumerateArray())
                        lines.Add($"→ {JsonUi.Str(step, "label")}: {JsonUi.Str(step, "detail")}");
                }
                ResultText.Text = string.Join("\n", lines);
                ShowNextSteps(ofxRes);
                return;
            }
            if (_csvFile is null) throw new InvalidOperationException("Pick a CSV or OFX/QFX first (or use Import PDF).");
            using var streamCsv = await _csvFile.OpenStreamForReadAsync();
            var res = await api.ImportBankCsvAsync(
                streamCsv,
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

    private async void ImportPdf_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        ResultText.Text = "";
        HideNextSteps();
        try
        {
            if (_pdfFile is null) throw new InvalidOperationException("Pick a PDF statement first.");
            if (AccountBox.SelectedItem is not ComboBoxItem ai || ai.Tag is not int accountId)
                throw new InvalidOperationException("Pick a target account.");
            var sign = "bank";
            if (SignBox.SelectedItem is ComboBoxItem si && si.Tag is string st)
                sign = st;

            using var stream = await _pdfFile.OpenStreamForReadAsync();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ImportStatementPdfAsync(
                stream,
                _pdfFile.Name,
                accountId,
                sign,
                AutoCatBox.IsChecked == true);
            ResultText.Text =
                $"PDF done · pages {Prop(res, "pages")} · lines {Prop(res, "lines_scanned")} · " +
                $"created {Prop(res, "transactions_created")} · skipped {Prop(res, "skipped_existing")} · " +
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
