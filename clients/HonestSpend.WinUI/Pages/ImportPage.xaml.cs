using System.Diagnostics;
using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Navigation;
using Windows.ApplicationModel.DataTransfer;
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
    /// <summary>Queued files from multi-select or drag-drop (any supported type).</summary>
    private readonly List<StorageFile> _pendingFiles = new();
    /// <summary>Last smart-import plan JSON (user can tweak combos then commit).</summary>
    private string? _smartPlanJson;
    private readonly List<(string EntityKey, ComboBox TypeBox, TextBox NameBox)> _smartEntityRows = new();
    private readonly List<(int FileIndex, string SourceKey, ComboBox EntityBox, ComboBox ActionBox, TextBox NickBox)> _smartAccountRows = new();
    private string? _inboxPath;
    private int? _setBooksAccountId;
    private int? _freezeAccountId;
    private bool _requireEndingBal;
    /// <summary>Remaining honesty CTAs after the active button (set_books then enter).</summary>
    private readonly List<(int AccountId, string Label)> _enterEndingQueue = new();
    private readonly List<(int AccountId, string Label)> _setBooksQueue = new();
    private readonly Dictionary<int, string> _accountKinds = new();

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
            _accountKinds.Clear();
            foreach (var a in accounts.EnumerateArray())
            {
                var id = a.GetProperty("id").GetInt32();
                var kind = JsonUi.Str(a, "kind");
                _accountKinds[id] = kind;
                AccountBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(a, "nickname")} · {UiCopy.AccountKind(kind)}",
                    Tag = id,
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
        SuccessBar.IsOpen = false;
        WarningBar.IsOpen = false;
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
            var advTotal = JsonUi.Int(res, "schedules_advanced", 0);
            string? advHint = NullIfEmptyHint(JsonUi.Str(res, "schedule_advance_hint", ""));
            string? advErr = NullIfEmptyHint(JsonUi.Str(res, "schedule_advance_error", ""));
            var hasTopLevelAdvance = advTotal > 0 || advHint is not null || advErr is not null;
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
                    // Nested only when top-level has no advance fields (avoid double-count)
                    if (!hasTopLevelAdvance)
                    {
                        var n = JsonUi.Int(r, "schedules_advanced", 0);
                        if (n > 0)
                        {
                            advTotal += n;
                            advHint ??= NullIfEmptyHint(JsonUi.Str(r, "schedule_advance_hint", ""));
                        }
                        advErr ??= NullIfEmptyHint(JsonUi.Str(r, "schedule_advance_error", ""));
                    }
                }
            }
            if (advTotal > 0 || advHint is not null || advErr is not null)
            {
                using var synth = JsonDocument.Parse(
                    System.Text.Json.JsonSerializer.Serialize(new
                    {
                        schedules_advanced = advTotal,
                        schedule_advance_hint = advHint,
                        schedule_advance_error = advErr,
                    }));
                ApplyScheduleAdvanceFeedback(synth.RootElement, lines);
            }
            AppendNextStepLines(lines, res);
            ResultText.Text = string.Join("\n", lines);
            ShowNextSteps(res);
            MaybeOfferFreezeFromInbox(res);
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

    private static readonly string[] AllImportExt =
        { ".csv", ".txt", ".ofx", ".qfx", ".pdf", ".xlsx" };

    private void ClearPrimaryFileSlots()
    {
        _csvFile = null;
        _ofxFile = null;
        _pdfFile = null;
        _xlsxFile = null;
    }

    private void SetPrimaryFromFile(StorageFile file)
    {
        ClearPrimaryFileSlots();
        var ext = (Path.GetExtension(file.Name) ?? "").ToLowerInvariant();
        switch (ext)
        {
            case ".ofx":
            case ".qfx":
                _ofxFile = file;
                break;
            case ".pdf":
                _pdfFile = file;
                break;
            case ".xlsx":
                _xlsxFile = file;
                break;
            default:
                _csvFile = file;
                break;
        }
    }

    private void RefreshPendingUi()
    {
        if (_pendingFiles.Count == 0)
        {
            CsvPathText.Text = "No file selected";
            PendingFilesList.ItemsSource = null;
            DropZoneHint.Text = "or tap to browse";
            return;
        }
        CsvPathText.Text = _pendingFiles.Count == 1
            ? _pendingFiles[0].Name
            : $"{_pendingFiles.Count} files ready to import";
        PendingFilesList.ItemsSource = _pendingFiles.Select(f => f.Name).ToList();
        DropZoneHint.Text = $"{_pendingFiles.Count} file(s) · drop more or Import";
        SetPrimaryFromFile(_pendingFiles[0]);
    }

    private void QueueFiles(IEnumerable<StorageFile> files)
    {
        foreach (var f in files)
        {
            var ext = (Path.GetExtension(f.Name) ?? "").ToLowerInvariant();
            if (AllImportExt.Contains(ext) && _pendingFiles.All(x => x.Path != f.Path))
                _pendingFiles.Add(f);
        }
        RefreshPendingUi();
        ClearEndingBalanceBox();
    }

    private void DropZone_DragOver(object sender, DragEventArgs e)
    {
        e.AcceptedOperation = DataPackageOperation.Copy;
        if (e.DragUIOverride is not null)
        {
            e.DragUIOverride.Caption = "Import bank file(s)";
            e.DragUIOverride.IsCaptionVisible = true;
        }
        e.Handled = true;
    }

    private async void DropZone_Drop(object sender, DragEventArgs e)
    {
        e.Handled = true;
        ErrorBar.IsOpen = false;
        try
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems))
                return;
            var items = await e.DataView.GetStorageItemsAsync();
            var files = items.OfType<StorageFile>().ToList();
            if (files.Count == 0)
                throw new InvalidOperationException("Drop files (not folders).");
            QueueFiles(files);
            if (_pendingFiles.Count > 0)
                await RunSmartPlanAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void DropZone_Tapped(object sender, TappedRoutedEventArgs e)
        => await PickAnyFilesAsync();

    private async void PickAny_Click(object sender, RoutedEventArgs e)
        => await PickAnyFilesAsync();

    private async Task PickAnyFilesAsync()
    {
        var files = await PickFilesAsync(AllImportExt);
        if (files.Count == 0) return;
        _pendingFiles.Clear();
        QueueFiles(files);
        await RunSmartPlanAsync();
    }

    private async void SmartImportPlan_Click(object sender, RoutedEventArgs e)
        => await RunSmartPlanAsync();

    private async Task RunSmartPlanAsync()
    {
        ErrorBar.IsOpen = false;
        if (_pendingFiles.Count == 0)
        {
            ErrorBar.Message = "Drop or pick bank file(s) first.";
            ErrorBar.IsOpen = true;
            return;
        }
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var streams = new List<(Stream Stream, string FileName)>();
            var opened = new List<IDisposable>();
            try
            {
                foreach (var f in _pendingFiles)
                {
                    var s = await f.OpenStreamForReadAsync();
                    opened.Add(s);
                    streams.Add((s, f.Name));
                }
                var plan = await api.SmartImportPlanAsync(streams);
                _smartPlanJson = plan.GetRawText();
                RenderSmartPlan(plan);
                SmartPlanPanel.Visibility = Visibility.Visible;
                PreviewText.Text = JsonUi.Str(plan, "summary") + "\n" + JsonUi.Str(plan, "hint");
            }
            finally
            {
                foreach (var d in opened)
                    try { d.Dispose(); } catch { /* ignore */ }
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void RenderSmartPlan(JsonElement plan)
    {
        SmartPlanSummary.Text = JsonUi.Str(plan, "summary");
        SmartPlanHint.Text = JsonUi.Str(plan, "hint");
        SmartEntitiesPanel.Children.Clear();
        SmartAccountsPanel.Children.Clear();
        _smartEntityRows.Clear();
        _smartAccountRows.Clear();

        if (plan.TryGetProperty("entities", out var ents) && ents.ValueKind == JsonValueKind.Array)
        {
            foreach (var e in ents.EnumerateArray())
            {
                var key = JsonUi.Str(e, "key", "personal");
                var et = JsonUi.Str(e, "entity_type", "personal");
                var name = JsonUi.Str(e, "display_name", et == "business" ? "Business" : "Personal");
                var conf = JsonUi.Str(e, "confidence", "");

                var row = new Border
                {
                    Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
                    BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                    BorderThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(8),
                    Padding = new Thickness(10),
                };
                var typeBox = new ComboBox { Header = "Entity type", MinWidth = 140 };
                typeBox.Items.Add(new ComboBoxItem { Content = "Personal", Tag = "personal", IsSelected = et is "personal" or "individual" });
                typeBox.Items.Add(new ComboBoxItem { Content = "Business", Tag = "business", IsSelected = et == "business" });
                if (typeBox.SelectedIndex < 0) typeBox.SelectedIndex = 0;
                var nameBox = new TextBox
                {
                    Header = "Name on the books",
                    Text = name,
                    PlaceholderText = et == "business" ? "e.g. AP Agency LLC" : "Personal",
                };
                var stack = new StackPanel { Spacing = 6 };
                stack.Children.Add(new TextBlock
                {
                    Text = $"Who · confidence {conf}",
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                });
                stack.Children.Add(typeBox);
                stack.Children.Add(nameBox);
                row.Child = stack;
                SmartEntitiesPanel.Children.Add(row);
                _smartEntityRows.Add((key, typeBox, nameBox));
            }
        }

        if (plan.TryGetProperty("sources", out var sources) && sources.ValueKind == JsonValueKind.Array)
        {
            foreach (var src in sources.EnumerateArray())
            {
                var fi = JsonUi.Int(src, "file_index", 0);
                var fname = JsonUi.Str(src, "filename");
                if (!src.TryGetProperty("accounts", out var accs) || accs.ValueKind != JsonValueKind.Array)
                    continue;
                foreach (var a in accs.EnumerateArray())
                {
                    var sk = JsonUi.Str(a, "source_key");
                    var ekey = JsonUi.Str(a, "entity_key", "personal");
                    var action = JsonUi.Str(a, "action", "create");
                    var nick = JsonUi.Str(a, "suggested_nickname", fname);
                    var kind = JsonUi.Str(a, "kind", "checking");
                    var reasons = "";
                    if (a.TryGetProperty("reasons", out var rs) && rs.ValueKind == JsonValueKind.Array)
                        reasons = string.Join(" · ", rs.EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrEmpty(x)).Take(3));

                    var row = new Border
                    {
                        Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
                        BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                        BorderThickness = new Thickness(1),
                        CornerRadius = new CornerRadius(8),
                        Padding = new Thickness(10),
                    };
                    var entityBox = new ComboBox { Header = "Belongs to", MinWidth = 160, HorizontalAlignment = HorizontalAlignment.Stretch };
                    foreach (var (ek, _, nameBox) in _smartEntityRows)
                    {
                        var label = string.IsNullOrWhiteSpace(nameBox.Text) ? ek : nameBox.Text;
                        entityBox.Items.Add(new ComboBoxItem
                        {
                            Content = label,
                            Tag = ek,
                            IsSelected = ek == ekey,
                        });
                    }
                    if (entityBox.Items.Count > 0 && entityBox.SelectedIndex < 0)
                        entityBox.SelectedIndex = 0;

                    var actionBox = new ComboBox { Header = "Account", MinWidth = 160 };
                    actionBox.Items.Add(new ComboBoxItem { Content = "Create new account", Tag = "create", IsSelected = action != "match" });
                    var matchLabel = "Match existing";
                    if (!string.IsNullOrEmpty(JsonUi.Str(a, "matched_nickname")))
                        matchLabel = "Match: " + JsonUi.Str(a, "matched_nickname");
                    actionBox.Items.Add(new ComboBoxItem
                    {
                        Content = matchLabel,
                        Tag = "match",
                        IsSelected = action == "match",
                        IsEnabled = a.TryGetProperty("account_id", out var aid) && aid.ValueKind == JsonValueKind.Number,
                    });
                    if (actionBox.SelectedIndex < 0) actionBox.SelectedIndex = 0;

                    var nickBox = new TextBox { Header = "Account nickname", Text = nick };
                    var stack = new StackPanel { Spacing = 6 };
                    stack.Children.Add(new TextBlock
                    {
                        Text = $"{fname} · {kind} · {JsonUi.Str(a, "transactions_found")} txns" +
                               (string.IsNullOrEmpty(JsonUi.Str(a, "ledger_balance")) ? "" : $" · bal ${JsonUi.Str(a, "ledger_balance")}"),
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        TextWrapping = TextWrapping.Wrap,
                    });
                    if (!string.IsNullOrEmpty(reasons))
                        stack.Children.Add(new TextBlock { Text = reasons, Opacity = 0.65, FontSize = 12, TextWrapping = TextWrapping.Wrap });
                    stack.Children.Add(entityBox);
                    stack.Children.Add(actionBox);
                    stack.Children.Add(nickBox);
                    row.Child = stack;
                    SmartAccountsPanel.Children.Add(row);
                    _smartAccountRows.Add((fi, sk, entityBox, actionBox, nickBox));
                }
            }
        }
    }

    private string BuildPlanJsonFromUi()
    {
        if (string.IsNullOrEmpty(_smartPlanJson))
            throw new InvalidOperationException("Run Analyze & map first.");
        using var doc = JsonDocument.Parse(_smartPlanJson);
        var root = doc.RootElement.Clone();
        // Rebuild plan with UI overrides via Dictionary serialization
        var plan = new Dictionary<string, object?>();
        foreach (var p in root.EnumerateObject())
        {
            if (p.Name is "entities" or "sources") continue;
            plan[p.Name] = JsonSerializer.Deserialize<object>(p.Value.GetRawText());
        }

        var entities = new List<Dictionary<string, object?>>();
        foreach (var (key, typeBox, nameBox) in _smartEntityRows)
        {
            var et = "personal";
            if (typeBox.SelectedItem is ComboBoxItem ti && ti.Tag is string t)
                et = t;
            int? profileId = null;
            if (root.TryGetProperty("entities", out var ents))
            {
                foreach (var e in ents.EnumerateArray())
                {
                    if (JsonUi.Str(e, "key") == key && e.TryGetProperty("profile_id", out var pid) && pid.ValueKind == JsonValueKind.Number)
                        profileId = pid.GetInt32();
                }
            }
            // Creating when name/type changed from existing
            var action = profileId is null ? "create" : "use_existing";
            // If user changed type to business and profile was personal, force create
            if (root.TryGetProperty("entities", out var ents2))
            {
                foreach (var e in ents2.EnumerateArray())
                {
                    if (JsonUi.Str(e, "key") != key) continue;
                    var oldEt = JsonUi.Str(e, "entity_type");
                    if (!string.Equals(oldEt, et, StringComparison.OrdinalIgnoreCase))
                    {
                        action = "create";
                        profileId = null;
                    }
                    else
                        action = JsonUi.Str(e, "action", action);
                }
            }
            entities.Add(new Dictionary<string, object?>
            {
                ["key"] = key,
                ["entity_type"] = et,
                ["display_name"] = nameBox.Text?.Trim() is { Length: > 0 } n ? n : (et == "business" ? "Business" : "Personal"),
                ["action"] = action,
                ["profile_id"] = profileId,
            });
        }
        plan["entities"] = entities;

        var sources = new List<Dictionary<string, object?>>();
        if (root.TryGetProperty("sources", out var srcArr))
        {
            foreach (var src in srcArr.EnumerateArray())
            {
                var fi = JsonUi.Int(src, "file_index", 0);
                var accounts = new List<Dictionary<string, object?>>();
                foreach (var a in src.GetProperty("accounts").EnumerateArray())
                {
                    var sk = JsonUi.Str(a, "source_key");
                    var row = _smartAccountRows.FirstOrDefault(r => r.FileIndex == fi && r.SourceKey == sk);
                    var dict = JsonSerializer.Deserialize<Dictionary<string, object?>>(a.GetRawText())
                               ?? new Dictionary<string, object?>();
                    if (row.EntityBox is not null)
                    {
                        if (row.EntityBox.SelectedItem is ComboBoxItem ei && ei.Tag is string ek)
                            dict["entity_key"] = ek;
                        if (row.ActionBox.SelectedItem is ComboBoxItem ai && ai.Tag is string act)
                            dict["action"] = act;
                        if (!string.IsNullOrWhiteSpace(row.NickBox.Text))
                            dict["suggested_nickname"] = row.NickBox.Text.Trim();
                    }
                    accounts.Add(dict);
                }
                sources.Add(new Dictionary<string, object?>
                {
                    ["file_index"] = fi,
                    ["filename"] = JsonUi.Str(src, "filename"),
                    ["accounts"] = accounts,
                });
            }
        }
        plan["sources"] = sources;
        return JsonSerializer.Serialize(plan);
    }

    private async void SmartImportCommit_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        SuccessBar.IsOpen = false;
        ResultText.Text = "";
        try
        {
            if (_pendingFiles.Count == 0)
                throw new InvalidOperationException("No files queued.");
            var planJson = BuildPlanJsonFromUi();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var streams = new List<(Stream Stream, string FileName)>();
            var opened = new List<IDisposable>();
            try
            {
                foreach (var f in _pendingFiles)
                {
                    var s = await f.OpenStreamForReadAsync();
                    opened.Add(s);
                    streams.Add((s, f.Name));
                }
                var sign = "bank";
                if (SignBox.SelectedItem is ComboBoxItem si && si.Tag is string st)
                    sign = st;
                var res = await api.SmartImportCommitAsync(
                    planJson,
                    streams,
                    sign,
                    AutoCatBox.IsChecked == true);
                var plain = JsonUi.Str(res, "customer_message");
                if (string.IsNullOrEmpty(plain) || plain is "?" or "—")
                    plain = JsonUi.Str(res, "summary");
                ResultText.Text =
                    plain + "\n\n" +
                    JsonUi.Str(res, "hint") + "\n" +
                    $"New: {JsonUi.Str(res, "transactions_created")} · " +
                    $"Duplicates skipped: {JsonUi.Str(res, "duplicates_skipped", "0")}";
                if (res.TryGetProperty("results", out var rr) && rr.ValueKind == JsonValueKind.Array)
                {
                    foreach (var r in rr.EnumerateArray().Take(24))
                    {
                        ResultText.Text += $"\n· {JsonUi.Str(r, "filename")} · " +
                            $"{JsonUi.Str(r, "format")} · +{JsonUi.Str(r, "transactions_created", "0")}" +
                            (JsonUi.Str(r, "skipped_existing", "0") is "0" or "?" or "—"
                                ? ""
                                : $" · skipped {JsonUi.Str(r, "skipped_existing")}");
                        if (!string.IsNullOrEmpty(JsonUi.Str(r, "error")) && JsonUi.Str(r, "error") is not ("?" or "—"))
                            ResultText.Text += $" · err {JsonUi.Str(r, "error")}";
                        // multi-account OFX detail
                        if (r.TryGetProperty("accounts", out var acs) && acs.ValueKind == JsonValueKind.Array)
                        {
                            foreach (var a in acs.EnumerateArray().Take(8))
                                ResultText.Text +=
                                    $"\n    · {JsonUi.Str(a, "nickname", JsonUi.Str(a, "acctid"))} " +
                                    $"+{JsonUi.Str(a, "transactions_created", "0")} " +
                                    $"(skip {JsonUi.Str(a, "skipped_existing", "0")})";
                        }
                    }
                }
                SuccessBar.Title = "Books updated";
                SuccessBar.Message = plain.Length > 120 ? plain[..120] + "…" : plain;
                SuccessBar.IsOpen = true;
                await LoadAsync();
            }
            finally
            {
                foreach (var d in opened)
                    try { d.Dispose(); } catch { /* ignore */ }
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void PickCsv_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".csv", ".txt" });
        if (file is null) return;
        _pendingFiles.Clear();
        QueueFiles(new[] { file });
    }

    private async void PickOfx_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".ofx", ".qfx" });
        if (file is null) return;
        _pendingFiles.Clear();
        QueueFiles(new[] { file });
    }

    private async void PickPdf_Click(object sender, RoutedEventArgs e)
    {
        var file = await PickFileAsync(new[] { ".pdf" });
        if (file is null) return;
        _pendingFiles.Clear();
        QueueFiles(new[] { file });
    }

    private async Task<IReadOnlyList<StorageFile>> PickFilesAsync(string[] extensions)
    {
        var picker = new FileOpenPicker();
        foreach (var ext in extensions)
            picker.FileTypeFilter.Add(ext);
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.ViewMode = PickerViewMode.List;
        var window = App.MainWindowInstance
            ?? throw new InvalidOperationException("Main window not ready.");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(window));
        var batch = await picker.PickMultipleFilesAsync();
        return batch?.ToList() ?? new List<StorageFile>();
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

            // Multi-file / drag-drop queue: preview each
            if (_pendingFiles.Count > 1)
            {
                var lines = new List<string> { $"{_pendingFiles.Count} files queued:" };
                foreach (var f in _pendingFiles)
                    lines.Add($"  · {f.Name}");
                lines.Add("Tap Import to process all (OFX multi-account auto-maps; CSV/PDF need Target account).");
                PreviewText.Text = string.Join("\n", lines);
                return;
            }

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
                    $"OFX/QFX · {JsonUi.Str(ofxRes, "transactions_found")} transactions · " +
                    $"{JsonUi.Str(ofxRes, "account_count", "1")} account(s)",
                    JsonUi.Str(ofxRes, "hint"),
                };
                if (ofxRes.TryGetProperty("accounts", out var accs) && accs.ValueKind == JsonValueKind.Array)
                {
                    foreach (var a in accs.EnumerateArray())
                    {
                        ofxLines.Add(
                            $"  · ACCTID {JsonUi.Str(a, "acctid")} · {JsonUi.Str(a, "kind")} · " +
                            $"{JsonUi.Str(a, "transactions_found")} txns" +
                            (string.IsNullOrEmpty(JsonUi.Str(a, "ledger_balance"))
                                ? ""
                                : $" · ledger ${JsonUi.Str(a, "ledger_balance")}"));
                    }
                }
                var ofxEnd = JsonUi.Str(ofxRes, "ledger_balance");
                if (!string.IsNullOrEmpty(ofxEnd) && ofxEnd is not ("—" or "?") &&
                    JsonUi.Str(ofxRes, "account_count", "1") == "1")
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
        HideFreezeStatement();
    }

    private void HideFreezeStatement()
    {
        FreezeStatementPanel.Visibility = Visibility.Collapsed;
        FreezeStatementBar.IsOpen = false;
        FreezeMsg.Text = "";
        _freezeAccountId = null;
    }

    private void DismissFreeze_Click(object sender, RoutedEventArgs e) => HideFreezeStatement();

    /// <summary>
    /// After credit import: optional freeze of bank statement actual for last close.
    /// Cash path unchanged (panel stays collapsed).
    /// </summary>
    private void MaybeOfferFreezeStatement(int accountId, JsonElement res)
    {
        if (!_accountKinds.TryGetValue(accountId, out var kind)
            || !string.Equals(kind, "credit", StringComparison.OrdinalIgnoreCase))
        {
            HideFreezeStatement();
            return;
        }

        _freezeAccountId = accountId;
        if (TryBalanceFromImport(res) is decimal bal)
            FreezeActualBox.Value = (double)bal;
        else
            FreezeActualBox.Value = double.NaN;

        FreezeMsg.Text = "";
        FreezeStatementBar.IsOpen = true;
        FreezeStatementPanel.Visibility = Visibility.Visible;
    }

    /// <summary>Inbox may touch several accounts — offer freeze for first credit hit.</summary>
    private void MaybeOfferFreezeFromInbox(JsonElement res)
    {
        if (!res.TryGetProperty("results", out var results) || results.ValueKind != JsonValueKind.Array)
        {
            HideFreezeStatement();
            return;
        }

        foreach (var r in results.EnumerateArray())
        {
            if (r.TryGetProperty("error", out var er)
                && er.ValueKind == JsonValueKind.String
                && !string.IsNullOrEmpty(er.GetString()))
                continue;
            if (!int.TryParse(JsonUi.Str(r, "account_id"), out var aid))
                continue;
            if (!_accountKinds.TryGetValue(aid, out var kind)
                || !string.Equals(kind, "credit", StringComparison.OrdinalIgnoreCase))
                continue;

            MaybeOfferFreezeStatement(aid, r);
            return;
        }

        HideFreezeStatement();
    }

    private static decimal? TryBalanceFromImport(JsonElement res)
    {
        foreach (var key in new[] { "ending_balance", "ledger_balance", "institution_balance" })
        {
            if (!res.TryGetProperty(key, out var p) || p.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
                continue;
            var raw = p.ValueKind switch
            {
                JsonValueKind.String => p.GetString(),
                JsonValueKind.Number => p.GetRawText(),
                _ => null,
            };
            if (string.IsNullOrWhiteSpace(raw) || raw is "?" or "—")
                continue;
            if (TryParseBankAmount(raw, out var bal))
                return bal;
        }
        return null;
    }

    private async void FreezeStatement_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (_freezeAccountId is not int accountId)
                throw new InvalidOperationException("No credit account for freeze.");
            if (double.IsNaN(FreezeActualBox.Value))
                throw new InvalidOperationException("Enter the statement balance from the bank.");
            var amt = (decimal)FreezeActualBox.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.FreezeStatementCycleAsync(accountId, new
            {
                actual_balance = amt,
                source = "import",
            });
            FreezeMsg.Text =
                $"Frozen close {JsonUi.Str(res, "cycle_end")} · actual {JsonUi.Money(res, "actual_balance")} · " +
                $"projected {JsonUi.Money(res, "projected_balance")} · variance {JsonUi.Money(res, "variance")}";
            FreezeStatementBar.IsOpen = false;
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
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
        SuccessBar.IsOpen = false;
        WarningBar.IsOpen = false;
        ResultText.Text = "";
        HideNextSteps();
        try
        {
            var sign = "bank";
            if (SignBox.SelectedItem is ComboBoxItem si && si.Tag is string st)
                sign = st;

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            // Prefer multi-file queue (drag-drop / pick many)
            var files = _pendingFiles.Count > 0
                ? _pendingFiles.ToList()
                : new List<StorageFile>();
            if (files.Count == 0)
            {
                if (_ofxFile is not null) files.Add(_ofxFile);
                else if (_csvFile is not null) files.Add(_csvFile);
                else if (_pdfFile is not null) files.Add(_pdfFile);
                else if (_xlsxFile is not null) files.Add(_xlsxFile);
            }
            if (files.Count == 0)
                throw new InvalidOperationException("Drop or pick bank file(s) first (CSV, OFX, QFX, PDF, XLSX).");

            var allLines = new List<string>();
            JsonElement? lastRes = null;
            int? lastAccountId = null;

            foreach (var file in files)
            {
                var ext = (Path.GetExtension(file.Name) ?? "").ToLowerInvariant();
                allLines.Add($"—— {file.Name} ——");

                if (ext is ".ofx" or ".qfx")
                {
                    using var stream = await file.OpenStreamForReadAsync();
                    // Multi-account OFX: omit account_id so engine maps/creates each ACCTID
                    var ofxRes = await api.ImportOfxAsync(
                        stream,
                        file.Name,
                        accountId: null,
                        amountSign: sign,
                        autoCategorize: AutoCatBox.IsChecked == true,
                        multi: true,
                        autoCreateAccounts: MultiOfxAutoCreateBox.IsChecked == true);
                    lastRes = ofxRes;
                    if (ofxRes.TryGetProperty("accounts", out var accts) && accts.ValueKind == JsonValueKind.Array)
                    {
                        allLines.Add(JsonUi.Str(ofxRes, "hint", "Multi-account OFX import done."));
                        foreach (var a in accts.EnumerateArray())
                        {
                            allLines.Add(
                                $"  · {JsonUi.Str(a, "nickname", JsonUi.Str(a, "acctid"))} · " +
                                $"+{Prop(a, "transactions_created")} new · found {Prop(a, "transactions_found")}" +
                                (string.IsNullOrEmpty(Prop(a, "ledger_balance")) ? "" : $" · ledger ${Prop(a, "ledger_balance")}"));
                            if (a.TryGetProperty("account_id", out var aidEl) && aidEl.TryGetInt32(out var aid))
                            {
                                lastAccountId = aid;
                                try
                                {
                                    await CompleteBankHonestyAfterImportAsync(api, aid, a, allLines);
                                }
                                catch { /* per-account trust best-effort */ }
                            }
                        }
                        ApplyScheduleAdvanceFeedback(ofxRes, allLines);
                    }
                    else
                    {
                        // Single-account style response
                        int accountId;
                        if (AccountBox.SelectedItem is ComboBoxItem ai && ai.Tag is int id)
                            accountId = id;
                        else
                            throw new InvalidOperationException("Pick a target account for single-account import.");
                        allLines.Add(
                            $"OFX/QFX · found {Prop(ofxRes, "transactions_found")} · created {Prop(ofxRes, "transactions_created")} · " +
                            $"skipped {Prop(ofxRes, "skipped_existing")}");
                        var trusted = await CompleteBankHonestyAfterImportAsync(api, accountId, ofxRes, allLines);
                        ApplyScheduleAdvanceFeedback(ofxRes, allLines);
                        lastAccountId = accountId;
                        if (trusted) { /* ok */ }
                    }
                    continue;
                }

                if (ext is ".pdf")
                {
                    if (AccountBox.SelectedItem is not ComboBoxItem pai || pai.Tag is not int pdfAccountId)
                        throw new InvalidOperationException("Pick a target account for PDF import.");
                    using var pdfStream = await file.OpenStreamForReadAsync();
                    var pdfRes = await api.ImportStatementPdfAsync(
                        pdfStream, file.Name, pdfAccountId, sign, AutoCatBox.IsChecked == true);
                    lastRes = pdfRes;
                    lastAccountId = pdfAccountId;
                    allLines.Add(
                        $"PDF · created {Prop(pdfRes, "transactions_created")} · found {Prop(pdfRes, "transactions_found")}");
                    await CompleteBankHonestyAfterImportAsync(api, pdfAccountId, pdfRes, allLines);
                    continue;
                }

                if (ext is ".xlsx")
                {
                    using var xStream = await file.OpenStreamForReadAsync();
                    var slug = "personal";
                    if (ProfileSlugBox.SelectedItem is ComboBoxItem psi && psi.Tag is string s)
                        slug = s;
                    var xRes = await api.ImportBudgetXlsxAsync(xStream, file.Name, slug);
                    lastRes = xRes;
                    allLines.Add($"Excel · {JsonUi.Str(xRes, "message", "imported")} · created {Prop(xRes, "transactions_created")}");
                    continue;
                }

                // CSV / txt — needs target account
                if (AccountBox.SelectedItem is not ComboBoxItem cai || cai.Tag is not int csvAccountId)
                    throw new InvalidOperationException(
                        "Pick a target account for CSV (or use multi-account OFX which auto-maps).");
                decimal? instBal = files.Count == 1 ? ParseOptionalEndingBalanceOrThrow() : null;
                using var streamCsv = await file.OpenStreamForReadAsync();
                var res = await api.ImportBankCsvAsync(
                    streamCsv,
                    file.Name,
                    csvAccountId,
                    sign,
                    AutoCatBox.IsChecked == true,
                    institutionBalance: instBal);
                lastRes = res;
                lastAccountId = csvAccountId;
                allLines.Add(
                    $"CSV · scanned {Prop(res, "rows_scanned")} · created {Prop(res, "transactions_created")} · " +
                    $"skipped {Prop(res, "skipped_existing")} · categorized {Prop(res, "categorized")}");
                var trustedCsv = await CompleteBankHonestyAfterImportAsync(api, csvAccountId, res, allLines);
                ApplyScheduleAdvanceFeedback(res, allLines);
                if (trustedCsv) { /* ok */ }
            }

            await LoadAsync(); // refresh accounts after auto-create
            ResultText.Text = string.Join("\n", allLines);
            if (lastRes is JsonElement lr)
            {
                ShowNextSteps(lr, skipEnterEndingBal: true, skipSetBooksFromBank: true);
                if (lastAccountId is int la)
                    MaybeOfferFreezeStatement(la, lr);
            }
            SuccessBar.Title = "Import finished";
            SuccessBar.Message = $"{files.Count} file(s) processed.";
            SuccessBar.IsOpen = true;
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
        SuccessBar.IsOpen = false;
        WarningBar.IsOpen = false;
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
            ApplyScheduleAdvanceFeedback(res, pdfLines);
            AppendNextStepLines(pdfLines, res, skipEnterEndingBal: trusted, skipSetBooksFromBank: trusted);
            ResultText.Text = string.Join("\n", pdfLines);
            ShowNextSteps(res, skipEnterEndingBal: trusted, skipSetBooksFromBank: trusted);
            MaybeOfferFreezeStatement(accountId, res);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    /// <summary>
    /// Surface bank→schedule advance after import (Coming up cleanup).
    /// Non-fatal: schedule_advance_error is a warning only.
    /// </summary>
    private void ApplyScheduleAdvanceFeedback(JsonElement res, List<string>? lines = null)
    {
        var advanced = JsonUi.Int(res, "schedules_advanced", 0);
        var hint = NullIfEmptyHint(JsonUi.Str(res, "schedule_advance_hint", "")) ?? "";
        var advErr = NullIfEmptyHint(JsonUi.Str(res, "schedule_advance_error", ""));

        if (advanced > 0 || !string.IsNullOrWhiteSpace(hint))
        {
            var msg = !string.IsNullOrWhiteSpace(hint)
                ? hint
                : $"Bank matched {advanced} bill(s) — removed from Coming up";
            // Avoid toasting the "no matches" soft hint as a success
            var isPositive = advanced > 0
                || hint.Contains("matched", StringComparison.OrdinalIgnoreCase)
                || hint.Contains("removed", StringComparison.OrdinalIgnoreCase);
            if (isPositive)
            {
                lines?.Add(msg);
                SuccessBar.Title = "Bills matched";
                SuccessBar.Message = msg;
                SuccessBar.Severity = InfoBarSeverity.Success;
                SuccessBar.IsOpen = true;
            }
            else if (!string.IsNullOrWhiteSpace(hint))
            {
                lines?.Add(hint);
            }
        }

        if (advErr is not null)
        {
            lines?.Add("Schedule advance note: " + advErr);
            WarningBar.Title = "Bill match skipped";
            WarningBar.Message = advErr;
            WarningBar.Severity = InfoBarSeverity.Warning;
            WarningBar.IsOpen = true;
        }
    }

    private static string? NullIfEmptyHint(string? s)
    {
        if (string.IsNullOrWhiteSpace(s) || s is "—" or "null" or "?")
            return null;
        return s;
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
