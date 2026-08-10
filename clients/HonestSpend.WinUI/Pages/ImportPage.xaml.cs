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
    private int? _setBooksAccountId;
    private bool _requireEndingBal;
    /// <summary>Remaining honesty CTAs after the active button (set_books then enter).</summary>
    private readonly List<(int AccountId, string Label)> _enterEndingQueue = new();
    private readonly List<(int AccountId, string Label)> _setBooksQueue = new();

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
        HideNextSteps();
        try
        {
            // Only pass default when user opts in — silent default poisons wrong accounts
            int? defaultAcct = null;
            if (InboxUseSelectedAccountBox.IsChecked == true
                && AccountBox.SelectedItem is ComboBoxItem { Tag: int id })
                defaultAcct = id;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ProcessImportInboxAsync(
                defaultAccountId: defaultAcct,
                autoCategorize: AutoCatBox.IsChecked == true);
            var lines = new List<string>
            {
                $"Inbox · {JsonUi.Str(res, "files_seen", "0")} file(s) · " +
                $"{JsonUi.Str(res, "transactions_created", "0")} created · " +
                $"categorized {JsonUi.Str(res, "categorized", "0")}" +
                (string.IsNullOrEmpty(JsonUi.Str(res, "ofx_acctid_matches")) || JsonUi.Str(res, "ofx_acctid_matches") == "0"
                    ? ""
                    : $" · OFX ACCTID matches {JsonUi.Str(res, "ofx_acctid_matches")}"),
            };
            if (res.TryGetProperty("results", out var results) && results.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in results.EnumerateArray().Take(12))
                {
                    var line =
                        $"{JsonUi.Str(r, "file")} → {JsonUi.Str(r, "account_nickname", "?")} · " +
                        $"+{JsonUi.Str(r, "transactions_created", "0")}" +
                        (string.IsNullOrEmpty(JsonUi.Str(r, "match_mode")) ? "" : $" · match:{JsonUi.Str(r, "match_mode")}");
                    if (r.TryGetProperty("error", out var er) && er.ValueKind == JsonValueKind.String)
                        line += " · " + er.GetString();
                    lines.Add(line);
                }
            }
            AppendNextStepLines(lines, res);
            ResultText.Text = string.Join("\n", lines);
            ShowNextSteps(res);
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

    private void ClearEndingBalanceBox()
    {
        EndingBalanceBox.Text = "";
    }

    private async void PickCsv_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".csv", ".txt" });
        if (file is null) return;
        _csvFile = file;
        _ofxFile = null;
        _pdfFile = null;
        ClearEndingBalanceBox();
        CsvPathText.Text = file.Name;
    }

    private async void PickOfx_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".ofx", ".qfx" });
        if (file is null) return;
        _ofxFile = file;
        _csvFile = null;
        _pdfFile = null;
        ClearEndingBalanceBox();
        CsvPathText.Text = file.Name + " (OFX/QFX)";
    }

    private async void PickPdf_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".pdf" });
        if (file is null) return;
        _pdfFile = file;
        _csvFile = null;
        _ofxFile = null;
        ClearEndingBalanceBox();
        CsvPathText.Text = file.Name + " (PDF)";
    }

    /// <summary>
    /// Parse optional ending-bal box. Empty → null. Non-empty must parse or throw.
    /// </summary>
    private decimal? ParseOptionalEndingBalanceOrThrow()
    {
        if (string.IsNullOrWhiteSpace(EndingBalanceBox.Text))
            return null;
        if (!TryParseBankAmount(EndingBalanceBox.Text, out var bal))
        {
            EndingBalanceBox.Focus(FocusState.Programmatic);
            throw new InvalidOperationException(
                "Bank ending balance is not a valid amount (try 1234.56, (50.00), or 50.00-).");
        }
        return bal;
    }

    private static bool FileSetInstitutionBalance(JsonElement res)
    {
        if (res.TryGetProperty("institution_balance_set", out var ibs)
            && ibs.ValueKind == JsonValueKind.True)
            return true;
        var end = Prop(res, "ending_balance");
        var ledger = Prop(res, "ledger_balance");
        return (!string.IsNullOrEmpty(end) && end is not ("?" or "—"))
            || (!string.IsNullOrEmpty(ledger) && ledger is not ("?" or "—"));
    }

    /// <summary>
    /// Open-rarely rule: if import set bank bal (file or typed), trust → Safe to spend once.
    /// Returns true when trust completed (skip set_books / enter CTAs; keep Sort).
    /// </summary>
    private async Task<bool> CompleteBankHonestyAfterImportAsync(
        LedgerApiClient api, int accountId, JsonElement res, List<string> lines)
    {
        if (FileSetInstitutionBalance(res))
        {
            var trust = await api.ReconcileTrustAsync(accountId, "institution");
            lines.Add(
                $"Safe to spend updated · books ${JsonUi.Str(trust, "books_balance")} (trusted bank from file).");
            return true;
        }
        var typed = ParseOptionalEndingBalanceOrThrow();
        if (typed is null)
            return false;
        await api.SetInstitutionBalanceAsync(accountId, typed.Value, markReconciled: false);
        var trust2 = await api.ReconcileTrustAsync(accountId, "institution");
        lines.Add(
            $"Bank ending bal ${typed.Value:0.00} (typed) · Safe to spend updated · books " +
            $"${JsonUi.Str(trust2, "books_balance")} (trusted bank).");
        return true;
    }

    private void SelectAccountById(int accountId)
    {
        for (var i = 0; i < AccountBox.Items.Count; i++)
        {
            if (AccountBox.Items[i] is ComboBoxItem { Tag: int id } && id == accountId)
            {
                AccountBox.SelectedIndex = i;
                return;
            }
        }
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
                var pdfEnd = JsonUi.Str(pdfRes, "ending_balance");
                if (!string.IsNullOrEmpty(pdfEnd) && pdfEnd != "—" && pdfEnd != "?")
                {
                    pdfLines.Add($"Ending balance from file: ${pdfEnd}");
                    EndingBalanceBox.Text = pdfEnd;
                }
                else
                    EndingBalanceBox.Text = "";
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
                var ofxEnd = JsonUi.Str(ofxRes, "ledger_balance");
                if (!string.IsNullOrEmpty(ofxEnd) && ofxEnd != "—" && ofxEnd != "?")
                {
                    ofxLines.Add($"Ending balance from file: ${ofxEnd}");
                    EndingBalanceBox.Text = ofxEnd;
                }
                else
                    EndingBalanceBox.Text = "";
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
                $"amount → {JsonUi.Str(map, "amount_col")} · debit → {JsonUi.Str(map, "debit_col")} · credit → {JsonUi.Str(map, "credit_col")}" +
                (string.IsNullOrEmpty(JsonUi.Str(map, "balance_col")) ? "" : $" · balance → {JsonUi.Str(map, "balance_col")}"),
            };
            var endBal = JsonUi.Str(csvRes, "ending_balance");
            if (!string.IsNullOrEmpty(endBal) && endBal != "—" && endBal != "?")
            {
                csvLines.Add($"Ending balance from file: ${endBal}");
                // Always rebind from this preview so stale overrides cannot poison import
                EndingBalanceBox.Text = endBal;
            }
            else
            {
                EndingBalanceBox.Text = "";
            }
            if (csvRes.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
            {
                foreach (var er in errs.EnumerateArray())
                    csvLines.Add("Error: " + er.GetString());
            }
            if (csvRes.TryGetProperty("sample", out var csvSample) && csvSample.ValueKind == JsonValueKind.Array)
            {
                csvLines.Add("Sample:");
                foreach (var row in csvSample.EnumerateArray().Take(6))
                    csvLines.Add($"  {JsonUi.Str(row, "date")} · {JsonUi.Str(row, "payee")} · {JsonUi.Str(row, "amount")}" +
                        (string.IsNullOrEmpty(JsonUi.Str(row, "balance")) ? "" : $" · bal {JsonUi.Str(row, "balance")}"));
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
        SetBooksFromBankBtn.Visibility = Visibility.Collapsed;
        GoSortBtn.Visibility = Visibility.Collapsed;
        GoHomeBtn.Visibility = Visibility.Collapsed;
        GoHomeBtn.Content = "Home";
        _setBooksAccountId = null;
        _requireEndingBal = false;
        _enterEndingQueue.Clear();
        _setBooksQueue.Clear();
    }

    private void ShowNextSteps(
        JsonElement res,
        bool skipEnterEndingBal = false,
        bool skipSetBooksFromBank = false)
    {
        HideNextSteps();
        if (!res.TryGetProperty("next_steps", out var steps) || steps.ValueKind != JsonValueKind.Array)
            return;

        var setBooksItems = new List<(int? AccountId, string Label)>();
        var enterItems = new List<(int? AccountId, string Label)>();
        var showSort = false;
        var showHome = false;
        var homeLabel = "Home";

        foreach (var st in steps.EnumerateArray())
        {
            var action = JsonUi.Str(st, "action");
            if (action == "set_books_from_bank" && !skipSetBooksFromBank)
            {
                int? aid = int.TryParse(JsonUi.Str(st, "account_id"), out var id) ? id : null;
                var lab = string.IsNullOrEmpty(JsonUi.Str(st, "label"))
                    ? "Set Safe to spend from bank"
                    : JsonUi.Str(st, "label");
                setBooksItems.Add((aid, lab));
            }
            else if (action == "review")
                showSort = true;
            else if (action == "enter_ending_bal" && !skipEnterEndingBal)
            {
                int? eaid = int.TryParse(JsonUi.Str(st, "account_id"), out var id) ? id : null;
                enterItems.Add((eaid, JsonUi.Str(st, "label", "Enter bank ending balance")));
            }
            else if (action is "home" or "hold" or "bills" or "reconcile")
            {
                showHome = true;
                if (action == "bills")
                    homeLabel = "Home · bills";
                else if (action == "reconcile")
                    homeLabel = "Home · match bal";
            }
        }

        var show = false;

        // Honesty priority: set_books (bank bal already known) wins over enter_ending_bal
        if (setBooksItems.Count > 0)
        {
            ActivateSetBooks(setBooksItems[0].AccountId, setBooksItems[0].Label);
            for (var i = 1; i < setBooksItems.Count; i++)
            {
                if (setBooksItems[i].AccountId is int qid)
                    _setBooksQueue.Add((qid, setBooksItems[i].Label));
            }
            foreach (var ent in enterItems)
            {
                if (ent.AccountId is int qid)
                    _enterEndingQueue.Add((qid, ent.Label));
            }
            AppendAlsoNeedNote();
            show = true;
        }
        else if (enterItems.Count > 0)
        {
            ActivateEnterEndingBal(enterItems[0].AccountId, enterItems[0].Label);
            for (var i = 1; i < enterItems.Count; i++)
            {
                if (enterItems[i].AccountId is int qid)
                    _enterEndingQueue.Add((qid, enterItems[i].Label));
            }
            AppendAlsoNeedNote();
            show = true;
        }

        if (showSort)
        {
            GoSortBtn.Visibility = Visibility.Visible;
            show = true;
        }
        if (showHome)
        {
            GoHomeBtn.Content = homeLabel;
            GoHomeBtn.Visibility = Visibility.Visible;
            show = true;
        }

        NextStepsPanel.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
    }

    private void AppendAlsoNeedNote()
    {
        var rest = new List<string>();
        rest.AddRange(_setBooksQueue.Select(e => e.Label));
        rest.AddRange(_enterEndingQueue.Select(e => e.Label));
        if (rest.Count == 0)
            return;
        ResultText.Text =
            (ResultText.Text ?? "") +
            "\nAlso need: " +
            string.Join("; ", rest);
    }

    private void ActivateSetBooks(int? accountId, string label)
    {
        if (accountId is int sa)
        {
            _setBooksAccountId = sa;
            SelectAccountById(sa);
        }
        else if (AccountBox.SelectedItem is ComboBoxItem { Tag: int selected })
            _setBooksAccountId = selected;
        _requireEndingBal = false;
        SetBooksFromBankBtn.Content = string.IsNullOrEmpty(label) || label == "—"
            ? "Set Safe to spend from bank"
            : label;
        SetBooksFromBankBtn.Visibility = Visibility.Visible;
    }

    private void ActivateEnterEndingBal(int? accountId, string label)
    {
        if (accountId is int eaid)
        {
            _setBooksAccountId = eaid;
            SelectAccountById(eaid);
        }
        _requireEndingBal = true;
        EndingBalanceBox.Text = "";
        EndingBalanceBox.Focus(FocusState.Programmatic);
        SetBooksFromBankBtn.Content = string.IsNullOrEmpty(label) || label == "—"
            ? "Save ending bal + set Safe to spend"
            : (label.Contains("Save", StringComparison.OrdinalIgnoreCase)
                ? label
                : "Save ending bal + set Safe to spend");
        SetBooksFromBankBtn.Visibility = Visibility.Visible;
    }

    /// <summary>Advance next set_books account after a successful trust.</summary>
    private bool TryActivateNextSetBooks()
    {
        if (_setBooksQueue.Count == 0)
            return false;
        var (id, label) = _setBooksQueue[0];
        _setBooksQueue.RemoveAt(0);
        ActivateSetBooks(id, label);
        AppendAlsoNeedNote();
        NextStepsPanel.Visibility = Visibility.Visible;
        return true;
    }

    /// <summary>Advance to next bal-less account after a successful save+trust.</summary>
    private bool TryActivateNextEnterEndingBal()
    {
        if (_enterEndingQueue.Count == 0)
            return false;
        var (id, label) = _enterEndingQueue[0];
        _enterEndingQueue.RemoveAt(0);
        ActivateEnterEndingBal(id, label);
        AppendAlsoNeedNote();
        NextStepsPanel.Visibility = Visibility.Visible;
        return true;
    }

    private async void SetBooksFromBank_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var accountId = _setBooksAccountId;
            if (accountId is null && AccountBox.SelectedItem is ComboBoxItem { Tag: int selected })
                accountId = selected;
            if (accountId is null)
                throw new InvalidOperationException("Pick the account that received the import.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            if (_requireEndingBal)
            {
                // Bal-less path: require typed ending bal, then trust
                var typedBal = ParseOptionalEndingBalanceOrThrow();
                if (typedBal is null)
                {
                    EndingBalanceBox.Focus(FocusState.Programmatic);
                    throw new InvalidOperationException(
                        "Enter the bank ending balance first (from your statement or online banking).");
                }
                await api.SetInstitutionBalanceAsync(accountId.Value, typedBal.Value, markReconciled: false);
            }
            // set_books-only: trust existing institution_balance — do not re-apply typed box

            var res = await api.ReconcileTrustAsync(accountId.Value, "institution");
            ResultText.Text =
                (ResultText.Text ?? "") +
                $"\nSafe to spend updated · books ${Prop(res, "books_balance")} (trusted bank).";

            if (TryActivateNextSetBooks())
                return;
            if (TryActivateNextEnterEndingBal())
                return;

            SetBooksFromBankBtn.Visibility = Visibility.Collapsed;
            _requireEndingBal = false;
            GoHomeBtn.Visibility = Visibility.Visible;
            NextStepsPanel.Visibility = Visibility.Visible;
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    /// <summary>Parse bank-style amounts: $1,234.56, (50.00), 50.00- → decimal.</summary>
    private static bool TryParseBankAmount(string raw, out decimal value)
    {
        value = 0m;
        if (string.IsNullOrWhiteSpace(raw))
            return false;
        var s = raw.Trim().Replace("$", "").Replace(",", "").Replace(" ", "");
        var neg = false;
        if (s.StartsWith('(') && s.EndsWith(')'))
        {
            neg = true;
            s = s[1..^1].Trim();
        }
        else if (s.EndsWith('-') && s.Length > 1)
        {
            neg = true;
            s = s[..^1].Trim();
        }
        if (!(decimal.TryParse(s, System.Globalization.NumberStyles.Number,
                System.Globalization.CultureInfo.InvariantCulture, out value)
            || decimal.TryParse(s, out value)))
            return false;
        if (neg)
            value = -Math.Abs(value);
        return true;
    }

    private void GoSort_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.NavigatePublic("review");
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
                var trusted = await CompleteBankHonestyAfterImportAsync(api, accountId, ofxRes, lines);
                AppendNextStepLines(lines, ofxRes, skipEnterEndingBal: trusted, skipSetBooksFromBank: trusted);
                ResultText.Text = string.Join("\n", lines);
                ShowNextSteps(ofxRes, skipEnterEndingBal: trusted, skipSetBooksFromBank: trusted);
                return;
            }
            if (_csvFile is null) throw new InvalidOperationException("Pick a CSV or OFX/QFX first (or use Import PDF).");
            // Typed box: parse with bank rules; fail loud if non-empty garbage (no silent drop)
            decimal? instBal = ParseOptionalEndingBalanceOrThrow();
            using var streamCsv = await _csvFile.OpenStreamForReadAsync();
            var res = await api.ImportBankCsvAsync(
                streamCsv,
                _csvFile.Name,
                accountId,
                sign,
                AutoCatBox.IsChecked == true,
                institutionBalance: instBal);
            var csvLines = new List<string>
            {
                $"CSV done · scanned {Prop(res, "rows_scanned")} · created {Prop(res, "transactions_created")} · " +
                $"skipped existing {Prop(res, "skipped_existing")} · bad {Prop(res, "skipped_bad")} · " +
                $"categorized {Prop(res, "categorized")}",
            };
            if (!string.IsNullOrEmpty(Prop(res, "ending_balance")) && Prop(res, "ending_balance") != "?")
            {
                csvLines.Add(
                    $"Bank ending bal ${Prop(res, "ending_balance")}" +
                    (string.IsNullOrEmpty(Prop(res, "balance_source")) ? "" : $" ({Prop(res, "balance_source")})") +
                    (string.IsNullOrEmpty(Prop(res, "drift")) || Prop(res, "drift") == "?"
                        ? ""
                        : $" · books drift ${Prop(res, "drift")}"));
            }
            if (res.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
            {
                var list = errs.EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrEmpty(x)).Take(8);
                var joined = string.Join("; ", list!);
                if (!string.IsNullOrEmpty(joined))
                    csvLines.Add("Errors: " + joined);
            }
            // File bal (column/override) or typed-only → one-tap trust (same rule as OFX/PDF)
            var csvTrusted = await CompleteBankHonestyAfterImportAsync(api, accountId, res, csvLines);
            AppendNextStepLines(csvLines, res, skipEnterEndingBal: csvTrusted, skipSetBooksFromBank: csvTrusted);
            ResultText.Text = string.Join("\n", csvLines);
            ShowNextSteps(res, skipEnterEndingBal: csvTrusted, skipSetBooksFromBank: csvTrusted);
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
            var pdfLines = new List<string>
            {
                $"PDF done · pages {Prop(res, "pages")} · lines {Prop(res, "lines_scanned")} · " +
                $"created {Prop(res, "transactions_created")} · skipped {Prop(res, "skipped_existing")} · " +
                $"categorized {Prop(res, "categorized")}",
            };
            if (!string.IsNullOrEmpty(Prop(res, "ending_balance")) && Prop(res, "ending_balance") != "?")
            {
                pdfLines.Add(
                    $"Bank ending bal ${Prop(res, "ending_balance")}" +
                    (string.IsNullOrEmpty(Prop(res, "balance_source")) ? "" : $" ({Prop(res, "balance_source")})") +
                    (string.IsNullOrEmpty(Prop(res, "drift")) || Prop(res, "drift") == "?"
                        ? ""
                        : $" · books drift ${Prop(res, "drift")}"));
            }
            if (res.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
            {
                var list = errs.EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrEmpty(x)).Take(8);
                var joined = string.Join("; ", list!);
                if (!string.IsNullOrEmpty(joined))
                    pdfLines.Add("Errors: " + joined);
            }
            var trusted = await CompleteBankHonestyAfterImportAsync(api, accountId, res, pdfLines);
            AppendNextStepLines(pdfLines, res, skipEnterEndingBal: trusted, skipSetBooksFromBank: trusted);
            ResultText.Text = string.Join("\n", pdfLines);
            ShowNextSteps(res, skipEnterEndingBal: trusted, skipSetBooksFromBank: trusted);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private static void AppendNextStepLines(
        List<string> lines,
        JsonElement res,
        bool skipEnterEndingBal = false,
        bool skipSetBooksFromBank = false)
    {
        if (!res.TryGetProperty("next_steps", out var steps) || steps.ValueKind != JsonValueKind.Array)
            return;
        foreach (var step in steps.EnumerateArray())
        {
            var action = JsonUi.Str(step, "action");
            if (skipSetBooksFromBank && action == "set_books_from_bank")
                continue;
            if (skipEnterEndingBal && action == "enter_ending_bal")
                continue;
            if (action is "hold" or "home")
                continue;
            lines.Add($"→ {JsonUi.Str(step, "label")}: {JsonUi.Str(step, "detail")}");
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
