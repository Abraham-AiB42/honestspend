using System.Diagnostics;
using System.Globalization;
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

/// <summary>
/// Smart setup wizard: path (Plaid / CSV / manual), Plaid keys+Link+AI,
/// CSV +cash loop with bank guides/import, then later phases.
/// </summary>
public sealed partial class FirstRunPage : Page
{
    private string _phase = "welcome";
    private string? _path;
    private int _progress;
    private bool _loading;

    // Manual path local steps (legacy first-run)
    private int _manualStep;
    private const int ManualMax = 6;

    // CSV cash loop: hub | type | details | guide | import
    private string _cashUi = "hub";
    private string _cashType = "checking";
    private int? _activeCashAccountId;
    private string _activeCashName = "";
    private JsonElement? _activeGuide;
    private TextBox? _cashNick;
    private NumberBox? _cashOpenBal;
    private ComboBox? _bankGuideBox;

    // Discover proposals: id -> (selected, payment_option)
    private readonly Dictionary<string, (bool Selected, string? PaymentOpt, JsonElement Raw)> _discoverItems = new();
    private readonly Dictionary<string, (bool Selected, JsonElement Raw)> _recurringItems = new();
    private List<(int Id, string Name)> _catChips = new();
    private string? _pendingPayeeKey;
    private List<int>? _pendingTxnIds;
    private string _pendingPayeeLabel = "";
    private readonly HashSet<string> _skippedPayees = new(StringComparer.OrdinalIgnoreCase);
    private bool _categorizeAutoRan;
    private readonly Dictionary<int, NumberBox> _budgetAmountBoxes = new();
    private NumberBox? _totalBufferBox;
    private readonly Dictionary<int, NumberBox> _acctBufferBoxes = new();

    private TextBox? _cashName;
    private TextBox? _inst;
    private NumberBox? _cashBal;
    private NumberBox? _buffer;
    private CheckBox? _wantCard;
    private TextBox? _cardName;
    private NumberBox? _cardBal;
    private NumberBox? _cardLimit;
    private NumberBox? _cardDue;
    private CheckBox? _wantBill;
    private TextBox? _billName;
    private NumberBox? _billAmt;
    private CalendarDatePicker? _billNext;
    private ComboBox? _importCadenceBox;
    private ComboBox? _importFocusBox;

    // Plaid / AI wizard fields
    private TextBox? _plaidClientId;
    private PasswordBox? _plaidSecret;
    private ComboBox? _plaidEnv;
    private TextBox? _aiKey;
    private ComboBox? _aiProvider;
    private string _linkUrl = "http://127.0.0.1:7420/static/plaid-link.html";

    private string _cashNameV = "Primary checking";
    private string? _instV;
    private decimal _cashBalV;
    private decimal _bufferV = 1000;
    private bool _wantCardV;
    private string _cardNameV = "Everyday card";
    private decimal _cardBalV;
    private decimal _cardLimitV = 5000;
    private int _cardDueV = 15;
    private bool _wantBillV;
    private string _billNameV = "Housing / rent";
    private decimal _billAmtV = 1500;
    private DateTimeOffset _billNextV = DateTimeOffset.Now.AddDays(14);
    private string _importCadenceV = "weekly";
    private string _importFocusV = "transactions";

    public FirstRunPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await RefreshStateAsync();
    }

    private async Task RefreshStateAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.GetSetupStateAsync();
            ApplyState(st);
            Render();
        }
        catch (Exception ex)
        {
            // Offline: fall back to local welcome
            _phase = "welcome";
            StepLabel.Text = "Engine starting…";
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            Render();
        }
    }

    private void ApplyState(JsonElement st)
    {
        _phase = JsonUi.Str(st, "phase", "welcome");
        _path = st.TryGetProperty("path", out var p) && p.ValueKind == JsonValueKind.String
            ? p.GetString()
            : null;
        _progress = 0;
        if (st.TryGetProperty("progress_pct", out var pct) && pct.TryGetInt32(out var n))
            _progress = n;
        ProgressBar.Value = _progress;
        if (_phase == "manual" && _manualStep == 0)
            _manualStep = 0;
        if (_phase == "done")
        {
            AppState.ShowSetupNav = false;
        }
    }

    private void Render()
    {
        ErrorBar.IsOpen = false;
        InfoBar.IsOpen = false;
        Fields.Children.Clear();
        BackBtn.IsEnabled = _phase is not ("welcome" or "done");
        NextBtn.Content = "Next";
        NextBtn.IsEnabled = true;
        ProgressBar.Value = _progress;
        StepLabel.Text = $"{_phase.Replace('_', ' ')} · {_progress}%";

        if (_phase == "done")
        {
            QuestionText.Text = "You're set";
            HintText.Text = "Open Home for Safe to spend and Do this next.";
            NextBtn.Content = "Go to Home";
            BackBtn.IsEnabled = false;
            return;
        }

        if (_phase == "welcome")
        {
            QuestionText.Text = "We'll answer one question";
            HintText.Text =
                "What can you safely spend without bouncing checking or paying dumb interest?\n\n" +
                "About 15–40 minutes if you import bank history — or 2 minutes for a quick manual start. " +
                "You can pause anytime; we remember where you left off.";
            NextBtn.Content = "Let's go";
            return;
        }

        if (_phase == "path")
        {
            QuestionText.Text = "How do you want to connect money?";
            HintText.Text =
                "Plaid uses your own free trial keys (up to 10 bank connections). " +
                "CSV never needs bank passwords inside HonestSpend. " +
                "Quick manual is fastest if you're in a hurry.";

            void addPath(string id, string title, string detail)
            {
                var btn = new Button
                {
                    Content = new StackPanel
                    {
                        Spacing = 2,
                        Children =
                        {
                            new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold },
                            new TextBlock { Text = detail, Opacity = 0.75, TextWrapping = TextWrapping.Wrap, FontSize = 12 },
                        },
                    },
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Left,
                    Padding = new Thickness(12),
                    Tag = id,
                    Margin = new Thickness(0, 0, 0, 4),
                };
                btn.Click += async (_, _) => await ChoosePathAsync(id);
                Fields.Children.Add(btn);
            }

            addPath("plaid", "Plaid (your keys)", "Signup → paste client id + secret → Link banks. Best for ongoing sync.");
            addPath("csv", "CSV / OFX imports", "Add cash accounts one-by-one with bank download guides. Free forever.");
            addPath("manual", "Quick manual (2 min)", "One checking, optional card & bill. Import later.");
            NextBtn.IsEnabled = false;
            NextBtn.Content = "Pick a path above";
            return;
        }

        if (_phase == "manual")
        {
            RenderManual();
            return;
        }

        if (_phase == "plaid_keys")
        {
            RenderPlaidKeys();
            return;
        }
        if (_phase == "plaid_link")
        {
            _ = RenderPlaidLinkAsync();
            return;
        }
        if (_phase == "ai_keys")
        {
            RenderAiKeys();
            return;
        }
        if (_phase is "cash_loop" or "import_cash")
        {
            _ = RenderCashLoopAsync();
            return;
        }
        if (_phase == "discover")
        {
            _ = RenderDiscoverAsync();
            return;
        }
        if (_phase == "liabilities")
        {
            RenderLiabilitiesHint();
            return;
        }
        if (_phase == "recurring")
        {
            _ = RenderRecurringAsync();
            return;
        }
        if (_phase == "categorize")
        {
            _ = RenderCategorizeAsync();
            return;
        }
        if (_phase == "budgets")
        {
            _ = RenderBudgetsAsync();
            return;
        }
        if (_phase == "buffers")
        {
            _ = RenderBuffersAsync();
            return;
        }

        QuestionText.Text = _phase.Replace('_', ' ');
        HintText.Text = "Continue setup or skip for now.";
        NextBtn.Content = "Next";
    }

    private void RenderPlaidKeys()
    {
        QuestionText.Text = "Your Plaid keys (local only)";
        HintText.Text =
            "HonestSpend never sees your bank password. Create a free Plaid account (Personal / trial — " +
            "about 10 institution links), copy client_id + secret, paste here. Keys stay on this PC.";
        NextBtn.Content = "Save & continue";

        var openSignup = new HyperlinkButton
        {
            Content = "Open Plaid signup (dashboard.plaid.com)",
            NavigateUri = new Uri("https://dashboard.plaid.com/signup"),
        };
        var openKeys = new HyperlinkButton
        {
            Content = "Open API keys page",
            NavigateUri = new Uri("https://dashboard.plaid.com/developers/keys"),
        };
        Fields.Children.Add(openSignup);
        Fields.Children.Add(openKeys);
        Fields.Children.Add(new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.8,
            Text =
                "1) Sign up → Team Settings / Developers → Keys\n" +
                "2) Start with Sandbox to practice, then Development or Production\n" +
                "3) Paste client_id and secret below — we store them encrypted locally (Windows DPAPI)",
        });

        _plaidClientId = new TextBox { Header = "client_id", PlaceholderText = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" };
        Fields.Children.Add(_plaidClientId);
        Fields.Children.Add(new TextBlock { Text = "secret", Opacity = 0.85 });
        _plaidSecret = new PasswordBox { PlaceholderText = "••••••••" };
        Fields.Children.Add(_plaidSecret);
        _plaidEnv = new ComboBox { Header = "Environment", HorizontalAlignment = HorizontalAlignment.Stretch };
        AddCombo(_plaidEnv, "sandbox", "Sandbox (practice)", true);
        AddCombo(_plaidEnv, "development", "Development (limited live)", false);
        AddCombo(_plaidEnv, "production", "Production", false);
        Fields.Children.Add(_plaidEnv);
    }

    private async Task RenderPlaidLinkAsync()
    {
        QuestionText.Text = "Link your banks";
        HintText.Text = "Opens Plaid Link in your browser. After connecting, return here and press Next.";
        NextBtn.Content = "I've linked — continue";
        Fields.Children.Clear();
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.GetPlaidStatusAsync();
            var enabled = st.TryGetProperty("enabled", out var en) && en.GetBoolean();
            var n = JsonUi.Int(st, "item_count", 0);
            var limit = JsonUi.Int(st, "item_limit", 10);
            _linkUrl = JsonUi.Str(st, "link_url", _linkUrl);

            Fields.Children.Add(new TextBlock
            {
                Text = enabled
                    ? $"Plaid ON · env {JsonUi.Str(st, "env")} · institutions {n}/{limit}"
                    : "Plaid OFF — go Back and save keys first.",
                TextWrapping = TextWrapping.Wrap,
            });
            if (st.TryGetProperty("at_item_limit", out var lim) && lim.GetBoolean())
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = $"Trial limit reached ({n}/{limit}). Disconnect a bank in Full books → Banks before adding more.",
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.OrangeRed),
                });
            }

            var open = new Button
            {
                Content = "Open Plaid Link in browser",
                Style = (Style)Application.Current.Resources["AccentButtonStyle"],
                HorizontalAlignment = HorizontalAlignment.Left,
                IsEnabled = enabled && !(st.TryGetProperty("at_item_limit", out var l2) && l2.GetBoolean()),
            };
            open.Click += (_, _) =>
            {
                try
                {
                    Process.Start(new ProcessStartInfo(_linkUrl) { UseShellExecute = true });
                }
                catch (Exception ex)
                {
                    ErrorBar.Message = ex.Message;
                    ErrorBar.IsOpen = true;
                }
            };
            Fields.Children.Add(open);
            Fields.Children.Add(new TextBlock
            {
                Opacity = 0.75,
                TextWrapping = TextWrapping.Wrap,
                Text = "Sandbox: use Plaid's test credentials in Link. Production: real banks with your Production keys.",
            });
        }
        catch (Exception ex)
        {
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private void RenderAiKeys()
    {
        QuestionText.Text = "AI helpers (optional)";
        HintText.Text =
            "After Plaid, you can add BYOK keys for smarter categorize later. " +
            "Grok (xAI) is first-class; OpenAI, Anthropic, or custom are stored too. All local-only. Skip anytime.";
        NextBtn.Content = "Save & continue (or skip empty)";

        _aiProvider = new ComboBox { Header = "Provider", HorizontalAlignment = HorizontalAlignment.Stretch };
        AddCombo(_aiProvider, "xai", "Grok (xAI)", true);
        AddCombo(_aiProvider, "openai", "OpenAI", false);
        AddCombo(_aiProvider, "anthropic", "Anthropic", false);
        AddCombo(_aiProvider, "custom", "Other / custom", false);
        _aiKey = new TextBox { Header = "API key", PlaceholderText = "sk-… or xai-…" };
        Fields.Children.Add(_aiProvider);
        Fields.Children.Add(_aiKey);
        Fields.Children.Add(new TextBlock
        {
            Opacity = 0.75,
            TextWrapping = TextWrapping.Wrap,
            Text = "Leave blank and press Next to skip. You can add keys later in Settings when that ships.",
        });
    }

    private async Task RenderCashLoopAsync()
    {
        NextBtn.IsEnabled = true;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.GetSetupCashAsync();

            if (_cashUi == "hub" || _phase == "import_cash" && _cashUi == "hub")
            {
                // On import_cash phase, jump to first needing import
                if (_phase == "import_cash" && st.TryGetProperty("need_import", out var ni)
                    && ni.ValueKind == JsonValueKind.Array && ni.GetArrayLength() > 0)
                {
                    var first = ni[0];
                    _activeCashAccountId = JsonUi.Int(first, "id", 0);
                    _activeCashName = JsonUi.Str(first, "nickname");
                    _cashUi = "import";
                }
            }

            switch (_cashUi)
            {
                case "type":
                    RenderCashTypePick();
                    break;
                case "details":
                    await RenderCashDetailsAsync(api);
                    break;
                case "guide":
                    RenderCashGuide();
                    break;
                case "import":
                    RenderCashImport();
                    break;
                default:
                    RenderCashHub(st);
                    break;
            }
        }
        catch (Exception ex)
        {
            QuestionText.Text = "Cash accounts";
            HintText.Text = ex.Message;
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private void RenderCashHub(JsonElement st)
    {
        QuestionText.Text = "Your cash accounts";
        HintText.Text =
            "Add checking, savings, or money market one at a time. " +
            "After each account we’ll show how to download a CSV from your bank (~90 days).";
        NextBtn.Content = "Next — find cards & bills";
        NextBtn.IsEnabled = JsonUi.Int(st, "count", 0) > 0
            || (st.TryGetProperty("has_cash", out var hc) && hc.ValueKind == JsonValueKind.True);

        if (st.TryGetProperty("accounts", out var accs) && accs.ValueKind == JsonValueKind.Array)
        {
            foreach (var a in accs.EnumerateArray())
            {
                var id = JsonUi.Int(a, "id", 0);
                var imported = a.TryGetProperty("imported", out var im) && im.GetBoolean();
                var line = new TextBlock
                {
                    Text =
                        $"• {JsonUi.Str(a, "nickname")} ({JsonUi.Str(a, "kind")}) · " +
                        $"{JsonUi.Str(a, "institution", "bank?")} · " +
                        (imported ? $"imported ({JsonUi.Int(a, "transaction_count", 0)} txns)" : "needs CSV"),
                    TextWrapping = TextWrapping.Wrap,
                };
                Fields.Children.Add(line);
                if (!imported && id > 0)
                {
                    var impBtn = new Button
                    {
                        Content = $"Import CSV for {JsonUi.Str(a, "nickname")}",
                        Tag = (id, JsonUi.Str(a, "nickname")),
                        Margin = new Thickness(0, 0, 0, 6),
                    };
                    impBtn.Click += (_, _) =>
                    {
                        if (impBtn.Tag is ValueTuple<int, string> t)
                        {
                            _activeCashAccountId = t.Item1;
                            _activeCashName = t.Item2;
                            _cashUi = "import";
                            Render();
                        }
                    };
                    Fields.Children.Add(impBtn);
                }
            }
            if (accs.GetArrayLength() == 0)
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = "No cash accounts yet — add your first checking account.",
                    Opacity = 0.8,
                    TextWrapping = TextWrapping.Wrap,
                });
                NextBtn.IsEnabled = false;
            }
        }

        var add = new Button
        {
            Content = "+ Cash account",
            Style = (Style)Application.Current.Resources["AccentButtonStyle"],
            HorizontalAlignment = HorizontalAlignment.Left,
            Margin = new Thickness(0, 8, 0, 0),
        };
        add.Click += (_, _) =>
        {
            _cashUi = "type";
            Render();
        };
        Fields.Children.Add(add);
        Fields.Children.Add(new TextBlock
        {
            Opacity = 0.7,
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 8, 0, 0),
            Text = JsonUi.Str(st, "import_hint", "Prefer ~90 days of transactions for bill detection."),
        });
    }

    private void RenderCashTypePick()
    {
        QuestionText.Text = "What kind of cash account?";
        HintText.Text = "Personal checking is usually first. Money market is tracked as savings.";
        NextBtn.IsEnabled = false;
        NextBtn.Content = "Pick a type";

        void addType(string id, string label)
        {
            var b = new Button
            {
                Content = label,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                Margin = new Thickness(0, 0, 0, 6),
                Tag = id,
            };
            b.Click += (_, _) =>
            {
                _cashType = id;
                _cashUi = "details";
                Render();
            };
            Fields.Children.Add(b);
        }
        addType("checking", "Checking");
        addType("savings", "Savings");
        addType("money_market", "Money market / HISA");
        var back = new Button { Content = "Back to list", Margin = new Thickness(0, 8, 0, 0) };
        back.Click += (_, _) => { _cashUi = "hub"; Render(); };
        Fields.Children.Add(back);
    }

    private async Task RenderCashDetailsAsync(LedgerApiClient api)
    {
        QuestionText.Text = "Name it & pick your bank";
        HintText.Text = "We’ll show download steps for that bank after you create the account.";
        NextBtn.Content = "Create account";
        NextBtn.IsEnabled = true;

        var defaultName = _cashType switch
        {
            "savings" => "Savings",
            "money_market" => "Money market",
            _ => "Primary checking",
        };
        _cashNick = new TextBox { Header = "Nickname", Text = defaultName };
        _cashOpenBal = new NumberBox { Header = "Balance today (optional $)", Value = 0, Minimum = 0 };
        _bankGuideBox = new ComboBox { Header = "Bank", HorizontalAlignment = HorizontalAlignment.Stretch };

        try
        {
            var guides = await api.GetBankGuidesAsync();
            if (guides.TryGetProperty("guides", out var g) && g.ValueKind == JsonValueKind.Array)
            {
                foreach (var guide in g.EnumerateArray())
                {
                    var id = JsonUi.Str(guide, "id");
                    var name = JsonUi.Str(guide, "name");
                    _bankGuideBox.Items.Add(new ComboBoxItem { Content = name, Tag = id });
                }
            }
        }
        catch { /* generic only */ }
        if (_bankGuideBox.Items.Count == 0)
            _bankGuideBox.Items.Add(new ComboBoxItem { Content = "Other bank / CU", Tag = "generic" });
        _bankGuideBox.SelectedIndex = 0;

        Fields.Children.Add(_cashNick);
        Fields.Children.Add(_cashOpenBal);
        Fields.Children.Add(_bankGuideBox);
        var cancel = new Button { Content = "Cancel", Margin = new Thickness(0, 8, 0, 0) };
        cancel.Click += (_, _) => { _cashUi = "hub"; Render(); };
        Fields.Children.Add(cancel);
    }

    private void RenderCashGuide()
    {
        QuestionText.Text = $"Download CSV for {_activeCashName}";
        HintText.Text = "About 90 days of transactions is ideal. We never store your bank password.";
        NextBtn.Content = "I've got the file — import";
        NextBtn.IsEnabled = true;

        if (_activeGuide is JsonElement g && g.ValueKind == JsonValueKind.Object)
        {
            var login = JsonUi.Str(g, "login_url", "");
            if (!string.IsNullOrEmpty(login) && login != "—")
            {
                var open = new HyperlinkButton
                {
                    Content = $"Open {JsonUi.Str(g, "name", "bank")} login",
                    NavigateUri = new Uri(login),
                };
                Fields.Children.Add(open);
            }
            if (g.TryGetProperty("steps", out var steps) && steps.ValueKind == JsonValueKind.Array)
            {
                var i = 1;
                foreach (var step in steps.EnumerateArray())
                {
                    Fields.Children.Add(new TextBlock
                    {
                        Text = $"{i}. {step.GetString()}",
                        TextWrapping = TextWrapping.Wrap,
                        Margin = new Thickness(0, 4, 0, 0),
                    });
                    i++;
                }
            }
            var notes = JsonUi.Str(g, "notes", "");
            if (!string.IsNullOrEmpty(notes) && notes != "—")
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = notes,
                    Opacity = 0.75,
                    TextWrapping = TextWrapping.Wrap,
                    Margin = new Thickness(0, 8, 0, 0),
                });
            }
        }
        else
        {
            Fields.Children.Add(new TextBlock
            {
                Text = "Sign in to online banking → account activity → Download / Export → CSV.",
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private void RenderCashImport()
    {
        QuestionText.Text = $"Import file → {_activeCashName}";
        HintText.Text = "Pick the CSV or OFX you downloaded. We’ll categorize what we can.";
        NextBtn.Content = "Pick file & import";
        NextBtn.IsEnabled = _activeCashAccountId is > 0;
    }

    private async Task CreateCashAccountAndContinueAsync()
    {
        var nick = _cashNick?.Text?.Trim() ?? "";
        var bal = 0m;
        if (_cashOpenBal is not null && !double.IsNaN(_cashOpenBal.Value))
            bal = (decimal)_cashOpenBal.Value;
        var guideId = "generic";
        if (_bankGuideBox?.SelectedItem is ComboBoxItem ci && ci.Tag is string gid)
            guideId = gid;

        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var res = await api.CreateSetupCashAccountAsync(new
        {
            account_type = _cashType,
            nickname = string.IsNullOrWhiteSpace(nick) ? null : nick,
            bank_guide_id = guideId,
            current_balance = bal,
        });
        if (res.TryGetProperty("account", out var acct))
        {
            _activeCashAccountId = JsonUi.Int(acct, "id", 0);
            _activeCashName = JsonUi.Str(acct, "nickname");
        }
        if (res.TryGetProperty("guide", out var guide) && guide.ValueKind == JsonValueKind.Object)
            _activeGuide = guide;
        _cashUi = "guide";
        Render();
    }

    private async Task ImportCashFileAsync()
    {
        if (_activeCashAccountId is not int acctId || acctId <= 0)
            throw new InvalidOperationException("No account selected for import.");

        var file = await PickCsvOrOfxAsync();
        if (file is null) return;

        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        await using var stream = await file.OpenStreamForReadAsync();
        JsonElement res;
        var name = file.Name;
        if (name.EndsWith(".ofx", StringComparison.OrdinalIgnoreCase)
            || name.EndsWith(".qfx", StringComparison.OrdinalIgnoreCase))
        {
            res = await api.ImportOfxAsync(stream, name, acctId);
        }
        else
        {
            res = await api.ImportBankCsvAsync(stream, name, acctId);
        }

        var created = JsonUi.Int(res, "transactions_created", 0);
        MsgText.Text =
            $"Imported {created} transactions" +
            (res.TryGetProperty("categorized", out var c) ? $", categorized {JsonUi.Int(res, "categorized", 0)}" : "") +
            ".";
        InfoBar.Title = "Import done";
        InfoBar.Message = MsgText.Text;
        InfoBar.IsOpen = true;
        _cashUi = "hub";
        Render();
    }

    private async Task<StorageFile?> PickCsvOrOfxAsync()
    {
        var picker = new FileOpenPicker();
        picker.FileTypeFilter.Add(".csv");
        picker.FileTypeFilter.Add(".ofx");
        picker.FileTypeFilter.Add(".qfx");
        picker.FileTypeFilter.Add(".txt");
        picker.SuggestedStartLocation = PickerLocationId.Downloads;
        picker.ViewMode = PickerViewMode.List;
        var window = App.MainWindowInstance
            ?? throw new InvalidOperationException("Main window not ready.");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(window));
        return await picker.PickSingleFileAsync();
    }

    private void RenderManual()
    {
        StepLabel.Text = $"Quick manual · step {_manualStep + 1} of {ManualMax + 1}";
        NextBtn.Content = _manualStep >= ManualMax ? "Finish" : "Next";
        BackBtn.IsEnabled = _manualStep > 0 || _phase != "welcome";

        switch (_manualStep)
        {
            case 0:
                QuestionText.Text = "Your primary checking";
                HintText.Text = "Rainy-day floor stays out of Safe to spend (default $1,000 total buffer for now).";
                _cashName = new TextBox { Header = "Nickname", Text = _cashNameV };
                _inst = new TextBox { Header = "Bank (optional)", Text = _instV ?? "" };
                _cashBal = new NumberBox { Header = "Balance today ($)", Value = (double)_cashBalV, Minimum = 0 };
                _buffer = new NumberBox { Header = "Total rainy-day floor ($)", Value = (double)_bufferV, Minimum = 0 };
                Fields.Children.Add(_cashName);
                Fields.Children.Add(_inst);
                Fields.Children.Add(_cashBal);
                Fields.Children.Add(_buffer);
                break;
            case 1:
                QuestionText.Text = "Add a credit card now?";
                HintText.Text = "Optional. Due day helps interest-free planning.";
                _wantCard = new CheckBox { Content = "Yes — add a card", IsChecked = _wantCardV };
                _wantCard.Checked += (_, _) => CardFields(true);
                _wantCard.Unchecked += (_, _) => CardFields(false);
                Fields.Children.Add(_wantCard);
                CardFields(_wantCardV);
                break;
            case 2:
                QuestionText.Text = "Biggest monthly bill?";
                HintText.Text = "Optional. Makes Safe to spend realistic on day one.";
                _wantBill = new CheckBox { Content = "Yes — add one bill", IsChecked = _wantBillV };
                _wantBill.Checked += (_, _) => BillFields(true);
                _wantBill.Unchecked += (_, _) => BillFields(false);
                Fields.Children.Add(_wantBill);
                BillFields(_wantBillV);
                break;
            case 3:
                QuestionText.Text = "How often should we remind you to refresh from your bank?";
                HintText.Text = "Download CSV/OFX yourself — we never store bank passwords.";
                _importCadenceBox = new ComboBox { Header = "Reminder cadence", HorizontalAlignment = HorizontalAlignment.Stretch };
                AddCombo(_importCadenceBox, "off", "Off", _importCadenceV == "off");
                AddCombo(_importCadenceBox, "daily", "Daily", _importCadenceV == "daily");
                AddCombo(_importCadenceBox, "weekly", "Weekly (default)", _importCadenceV is "weekly" or null or "");
                AddCombo(_importCadenceBox, "monthly", "Monthly", _importCadenceV == "monthly");
                _importFocusBox = new ComboBox { Header = "What to download", HorizontalAlignment = HorizontalAlignment.Stretch };
                AddCombo(_importFocusBox, "transactions", "Transactions CSV/OFX", _importFocusV is "transactions" or null or "");
                AddCombo(_importFocusBox, "statements", "Statements", _importFocusV == "statements");
                AddCombo(_importFocusBox, "both", "Both", _importFocusV == "both");
                Fields.Children.Add(_importCadenceBox);
                Fields.Children.Add(_importFocusBox);
                break;
            case 4:
                QuestionText.Text = "Review";
                HintText.Text = "We'll create these and mark setup complete.";
                var lines = new List<string>
                {
                    $"Checking: {_cashNameV} · {_cashBalV:C}",
                    $"Total buffer: {_bufferV:C}",
                };
                if (_wantCardV)
                    lines.Add($"Card: {_cardNameV} · owed {_cardBalV:C} · due day {_cardDueV}");
                if (_wantBillV)
                    lines.Add($"Bill: {_billNameV} · {_billAmtV:C}/mo");
                Fields.Children.Add(new ItemsControl { ItemsSource = lines });
                break;
            default:
                QuestionText.Text = "You're set";
                HintText.Text = MsgText.Text.Length > 0 ? MsgText.Text : "Open Home for Safe to spend.";
                NextBtn.Content = "Go to Home";
                break;
        }
    }

    private static void AddCombo(ComboBox box, string tag, string label, bool selected)
    {
        var item = new ComboBoxItem { Content = label, Tag = tag };
        box.Items.Add(item);
        if (selected) box.SelectedItem = item;
        if (box.SelectedItem is null && box.Items.Count == 1)
            box.SelectedIndex = 0;
    }

    private void CardFields(bool show)
    {
        while (Fields.Children.Count > 1)
            Fields.Children.RemoveAt(Fields.Children.Count - 1);
        if (!show) return;
        _cardName = new TextBox { Header = "Card nickname", Text = _cardNameV };
        _cardBal = new NumberBox { Header = "Balance owed ($)", Value = (double)_cardBalV, Minimum = 0 };
        _cardLimit = new NumberBox { Header = "Credit limit ($)", Value = (double)_cardLimitV, Minimum = 0 };
        _cardDue = new NumberBox { Header = "Payment due day (1–31)", Value = _cardDueV, Minimum = 1, Maximum = 31 };
        Fields.Children.Add(_cardName);
        Fields.Children.Add(_cardBal);
        Fields.Children.Add(_cardLimit);
        Fields.Children.Add(_cardDue);
    }

    private void BillFields(bool show)
    {
        while (Fields.Children.Count > 1)
            Fields.Children.RemoveAt(Fields.Children.Count - 1);
        if (!show) return;
        _billName = new TextBox { Header = "Bill name", Text = _billNameV };
        _billAmt = new NumberBox { Header = "Amount ($)", Value = (double)_billAmtV, Minimum = 0.01 };
        _billNext = new CalendarDatePicker { Header = "Next due", Date = _billNextV };
        Fields.Children.Add(_billName);
        Fields.Children.Add(_billAmt);
        Fields.Children.Add(_billNext);
    }

    private void CaptureManual()
    {
        if (_cashName is not null) _cashNameV = _cashName.Text?.Trim() ?? "Primary checking";
        if (_inst is not null) _instV = string.IsNullOrWhiteSpace(_inst.Text) ? null : _inst.Text.Trim();
        if (_cashBal is not null && !double.IsNaN(_cashBal.Value)) _cashBalV = (decimal)_cashBal.Value;
        if (_buffer is not null && !double.IsNaN(_buffer.Value)) _bufferV = (decimal)_buffer.Value;
        if (_wantCard is not null) _wantCardV = _wantCard.IsChecked == true;
        if (_cardName is not null) _cardNameV = _cardName.Text?.Trim() ?? "Card";
        if (_cardBal is not null && !double.IsNaN(_cardBal.Value)) _cardBalV = (decimal)_cardBal.Value;
        if (_cardLimit is not null && !double.IsNaN(_cardLimit.Value)) _cardLimitV = (decimal)_cardLimit.Value;
        if (_cardDue is not null && !double.IsNaN(_cardDue.Value)) _cardDueV = (int)_cardDue.Value;
        if (_wantBill is not null) _wantBillV = _wantBill.IsChecked == true;
        if (_billName is not null) _billNameV = _billName.Text?.Trim() ?? "Bill";
        if (_billAmt is not null && !double.IsNaN(_billAmt.Value)) _billAmtV = (decimal)_billAmt.Value;
        if (_billNext?.Date is not null) _billNextV = _billNext.Date.Value;
        if (_importCadenceBox?.SelectedItem is ComboBoxItem ci && ci.Tag is string cad)
            _importCadenceV = cad;
        if (_importFocusBox?.SelectedItem is ComboBoxItem fi && fi.Tag is string foc)
            _importFocusV = foc;
    }

    private async Task ChoosePathAsync(string path)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.SetupAdvanceAsync("set_path", path: path);
            ApplyState(st);
            _manualStep = 0;
            Render();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Back_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (_phase == "manual" && _manualStep > 0)
            {
                CaptureManual();
                _manualStep--;
                Render();
                return;
            }

            if ((_phase is "cash_loop" or "import_cash") && _cashUi is not "hub")
            {
                _cashUi = _cashUi switch
                {
                    "import" => _activeGuide is not null ? "guide" : "hub",
                    "guide" => "hub",
                    "details" => "type",
                    "type" => "hub",
                    _ => "hub",
                };
                Render();
                return;
            }

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var st = await api.SetupAdvanceAsync("back");
            ApplyState(st);
            Render();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Next_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        if (_loading) return;
        try
        {
            _loading = true;
            if (_phase == "done")
            {
                Frame?.Navigate(typeof(HomePage));
                return;
            }

            if (_phase == "path")
                return; // must pick a button

            if (_phase == "manual")
            {
                await ManualNextAsync();
                return;
            }

            if (_phase == "welcome")
            {
                using var api = new LedgerApiClient();
                await api.EnsureBackendAsync();
                var st = await api.SetupAdvanceAsync("next");
                ApplyState(st);
                Render();
                return;
            }

            if (_phase == "plaid_keys")
            {
                await SavePlaidKeysAndAdvanceAsync();
                return;
            }

            if (_phase == "ai_keys")
            {
                await SaveAiKeysAndAdvanceAsync();
                return;
            }

            if (_phase is "cash_loop" or "import_cash")
            {
                await CashLoopNextAsync();
                return;
            }

            if (_phase == "discover")
            {
                await DiscoverApplyAndAdvanceAsync();
                return;
            }

            if (_phase == "recurring")
            {
                await RecurringApplyAndAdvanceAsync();
                return;
            }

            if (_phase == "categorize")
            {
                using var api = new LedgerApiClient();
                await api.EnsureBackendAsync();
                var st = await api.SetupAdvanceAsync("next");
                ApplyState(st);
                Render();
                return;
            }

            if (_phase == "budgets")
            {
                await SaveBudgetsAndAdvanceAsync();
                return;
            }

            if (_phase == "buffers")
            {
                await SaveBuffersAndFinishAsync();
                return;
            }

            // plaid_link + other: advance server state
            using (var api = new LedgerApiClient())
            {
                await api.EnsureBackendAsync();
                var st = await api.SetupAdvanceAsync("next");
                ApplyState(st);
                if (_phase == "done")
                    AppState.ShowSetupNav = false;
                Render();
            }
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

    private async Task ManualNextAsync()
    {
        CaptureManual();
        if (_manualStep == 0 && string.IsNullOrWhiteSpace(_cashNameV))
            throw new InvalidOperationException("Name your checking account.");
        if (_manualStep == 1 && _wantCardV && (_cardDueV < 1 || _cardDueV > 31))
            throw new InvalidOperationException("Card needs a payment due day (1–31).");
        if (_manualStep == 2 && _wantBillV && _billAmtV <= 0)
            throw new InvalidOperationException("Bill amount must be greater than zero.");

        if (_manualStep < 4)
        {
            _manualStep++;
            Render();
            return;
        }

        if (_manualStep == 4)
        {
            await SubmitManualAsync();
            _manualStep = 5;
            Render();
            return;
        }

        Frame?.Navigate(typeof(HomePage));
    }

    private async Task SubmitManualAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var body = new Dictionary<string, object?>
        {
            ["profile_slug"] = "personal",
            ["cash_name"] = _cashNameV,
            ["cash_balance"] = _cashBalV,
            ["cash_institution"] = _instV,
            ["safety_buffer"] = _bufferV,
            ["ifpp_mode"] = "conservative",
            ["import_reminder_cadence"] = _importCadenceV,
            ["import_reminder_focus"] = _importFocusV,
        };
        if (_wantCardV)
        {
            body["card_name"] = _cardNameV;
            body["card_balance"] = _cardBalV;
            body["card_limit"] = _cardLimitV;
            body["card_due_day"] = _cardDueV;
        }
        if (_wantBillV)
        {
            body["bill_name"] = _billNameV;
            body["bill_amount"] = _billAmtV;
            body["bill_next_date"] = _billNextV.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        var res = await api.FirstRunAsync(body);
        MsgText.Text = JsonUi.Str(res, "message");
        if (string.IsNullOrEmpty(MsgText.Text) || MsgText.Text == "—")
            MsgText.Text = "Accounts created. Safe to spend is ready on Home.";
        // first-run marks setup done on server
        var st = await api.GetSetupStateAsync();
        ApplyState(st);
        AppState.ShowSetupNav = false;
    }

    private async Task SavePlaidKeysAndAdvanceAsync()
    {
        var clientId = _plaidClientId?.Text?.Trim() ?? "";
        var secret = _plaidSecret?.Password?.Trim() ?? "";
        var env = "sandbox";
        if (_plaidEnv?.SelectedItem is ComboBoxItem ci && ci.Tag is string t)
            env = t;
        if (string.IsNullOrWhiteSpace(clientId) || string.IsNullOrWhiteSpace(secret))
            throw new InvalidOperationException("Enter both client_id and secret, or go Back to pick CSV/manual.");

        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        await api.SavePlaidCredentialsAsync(clientId, secret, env);
        var st = await api.SetupAdvanceAsync("next", payload: new { plaid_env = env });
        ApplyState(st);
        Render();
    }

    private async Task SaveAiKeysAndAdvanceAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var key = _aiKey?.Text?.Trim() ?? "";
        if (!string.IsNullOrWhiteSpace(key))
        {
            var provider = "xai";
            if (_aiProvider?.SelectedItem is ComboBoxItem ci && ci.Tag is string t)
                provider = t;
            await api.SaveAiCredentialsAsync(provider, key);
        }
        var st = await api.SetupAdvanceAsync("next");
        ApplyState(st);
        Render();
    }

    private async Task CashLoopNextAsync()
    {
        if (_cashUi == "details")
        {
            await CreateCashAccountAndContinueAsync();
            return;
        }
        if (_cashUi == "guide")
        {
            _cashUi = "import";
            Render();
            return;
        }
        if (_cashUi == "import")
        {
            await ImportCashFileAsync();
            return;
        }
        if (_cashUi == "type")
            return; // must pick type button

        // hub: advance wizard (skip import_cash if already past / no pending)
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var cash = await api.GetSetupCashAsync();
        if (JsonUi.Int(cash, "count", 0) <= 0)
            throw new InvalidOperationException("Add at least one cash account, or Skip setup.");

        // From cash_loop → next phases; skip import_cash if all imported
        var st = await api.SetupAdvanceAsync("next");
        ApplyState(st);
        if (_phase == "import_cash")
        {
            var allImp = cash.TryGetProperty("all_imported", out var ai) && ai.GetBoolean();
            if (allImp || JsonUi.Int(cash, "count", 0) == 0)
            {
                st = await api.SetupAdvanceAsync("next");
                ApplyState(st);
            }
            else
            {
                _cashUi = "hub";
            }
        }
        else
        {
            _cashUi = "hub";
        }
        Render();
    }

    private async Task RenderDiscoverAsync()
    {
        QuestionText.Text = "Cards, loans & bills from your cash history";
        HintText.Text =
            "We skimmed payments from checking/savings. Confirm what to create. " +
            "Cards need a payment plan; investments become recurring debits.";
        NextBtn.Content = "Create selected & continue";
        _discoverItems.Clear();

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var disc = await api.GetSetupDiscoverAsync();
            Fields.Children.Add(new TextBlock
            {
                Text = JsonUi.Str(disc, "message"),
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.85,
                Margin = new Thickness(0, 0, 0, 8),
            });

            if (!disc.TryGetProperty("proposals", out var props) || props.ValueKind != JsonValueKind.Array
                || props.GetArrayLength() == 0)
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = "Nothing clear yet — import more cash history, or continue and add bills later.",
                    TextWrapping = TextWrapping.Wrap,
                });
                NextBtn.Content = "Continue without adding";
                return;
            }

            foreach (var p in props.EnumerateArray())
            {
                var id = JsonUi.Str(p, "id");
                if (string.IsNullOrEmpty(id) || id == "—")
                    id = Guid.NewGuid().ToString("N");
                var selected = !(p.TryGetProperty("selected", out var sel) && sel.ValueKind == JsonValueKind.False);
                var payDef = JsonUi.Str(p, "default_payment_option", "interest_saving");
                if (payDef == "—") payDef = "interest_saving";
                _discoverItems[id] = (selected, payDef, p);

                var type = JsonUi.Str(p, "type");
                var panel = new StackPanel { Spacing = 4, Margin = new Thickness(0, 0, 0, 10) };
                var cb = new CheckBox
                {
                    Content =
                        $"{JsonUi.Str(p, "name")} · {type} · ${JsonUi.Str(p, "median_amount")} · " +
                        $"{JsonUi.Str(p, "cadence")} ({JsonUi.Int(p, "occurrences", 0)}×)",
                    IsChecked = selected,
                    Tag = id,
                };
                cb.Checked += (_, _) =>
                {
                    if (cb.Tag is string tid && _discoverItems.TryGetValue(tid, out var cur))
                        _discoverItems[tid] = (true, cur.PaymentOpt, cur.Raw);
                };
                cb.Unchecked += (_, _) =>
                {
                    if (cb.Tag is string tid && _discoverItems.TryGetValue(tid, out var cur))
                        _discoverItems[tid] = (false, cur.PaymentOpt, cur.Raw);
                };
                panel.Children.Add(cb);
                panel.Children.Add(new TextBlock
                {
                    Text = JsonUi.Str(p, "reason"),
                    Opacity = 0.7,
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                    Margin = new Thickness(28, 0, 0, 0),
                });

                if (type is "credit" or "loan")
                {
                    var payBox = new ComboBox
                    {
                        Header = "Payment plan",
                        Tag = id,
                        HorizontalAlignment = HorizontalAlignment.Left,
                        MinWidth = 220,
                        Margin = new Thickness(28, 0, 0, 0),
                    };
                    void addPay(string tag, string label, bool on)
                    {
                        var it = new ComboBoxItem { Content = label, Tag = tag };
                        payBox.Items.Add(it);
                        if (on) payBox.SelectedItem = it;
                    }
                    addPay("interest_saving", "Interest-saving (recommended)", payDef == "interest_saving");
                    addPay("statement", "Statement balance", payDef == "statement");
                    addPay("fixed", "Fixed payment", payDef == "fixed");
                    addPay("minimum", "Minimum payment", payDef == "minimum");
                    if (payBox.SelectedItem is null && payBox.Items.Count > 0)
                        payBox.SelectedIndex = 0;
                    payBox.SelectionChanged += (_, _) =>
                    {
                        if (payBox.Tag is string tid && payBox.SelectedItem is ComboBoxItem ci
                            && ci.Tag is string opt && _discoverItems.TryGetValue(tid, out var cur))
                            _discoverItems[tid] = (cur.Selected, opt, cur.Raw);
                    };
                    panel.Children.Add(payBox);
                }

                Fields.Children.Add(panel);
            }
        }
        catch (Exception ex)
        {
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private void RenderLiabilitiesHint()
    {
        QuestionText.Text = "Debts & payment plans";
        HintText.Text =
            "Accounts you accepted are created. You can change payment plans anytime under Accounts. " +
            "Next reviews remaining recurring suggestions.";
        NextBtn.Content = "Continue";
        Fields.Children.Add(new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Text =
                "Payment options:\n" +
                "• Interest-saving — prioritize full promo / interest-free path (default)\n" +
                "• Statement balance — pay the statement each cycle\n" +
                "• Fixed — set a fixed dollar payment\n" +
                "• Minimum — min only (APR risk warning later)",
        });
    }

    private async Task DiscoverApplyAndAdvanceAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();

        var accepted = new List<Dictionary<string, object?>>();
        foreach (var kv in _discoverItems)
        {
            var (sel, payOpt, raw) = kv.Value;
            if (!sel) continue;
            var type = JsonUi.Str(raw, "type");
            var row = new Dictionary<string, object?>
            {
                ["type"] = type,
                ["name"] = JsonUi.Str(raw, "name"),
                ["median_amount"] = JsonUi.Str(raw, "median_amount", "0"),
                ["cadence"] = JsonUi.Str(raw, "cadence", "monthly"),
                ["suggested_next_date"] = JsonUi.Str(raw, "suggested_next_date"),
                ["selected"] = true,
            };
            if (type is "credit" or "loan")
                row["payment_option"] = payOpt ?? "interest_saving";
            accepted.Add(row);
        }

        if (accepted.Count > 0)
        {
            var res = await api.ApplySetupDiscoverAsync(new { accepted });
            MsgText.Text = JsonUi.Str(res, "message");
            InfoBar.Title = "Created";
            InfoBar.Message = MsgText.Text;
            InfoBar.IsOpen = true;
        }

        var st = await api.SetupAdvanceAsync("next");
        ApplyState(st);
        // Skip empty liabilities intermediate if we already applied
        if (_phase == "liabilities")
        {
            st = await api.SetupAdvanceAsync("next");
            ApplyState(st);
        }
        Render();
    }

    private async Task RenderRecurringAsync()
    {
        QuestionText.Text = "Any more recurring bills?";
        HintText.Text = "Patterns still on cash that aren’t scheduled yet. Uncheck noise; accept real bills.";
        NextBtn.Content = "Save selected & continue";
        _recurringItems.Clear();

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var rec = await api.GetSetupRecurringAsync();
            Fields.Children.Add(new TextBlock
            {
                Text = $"{JsonUi.Str(rec, "message")} · active bills: {JsonUi.Int(rec, "active_bills", 0)}",
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });

            if (!rec.TryGetProperty("suggestions", out var sug) || sug.ValueKind != JsonValueKind.Array
                || sug.GetArrayLength() == 0)
            {
                NextBtn.Content = "Continue";
                return;
            }

            foreach (var s in sug.EnumerateArray())
            {
                var name = JsonUi.Str(s, "name");
                var key = JsonUi.Str(s, "normalized", name);
                _recurringItems[key] = (true, s);
                var cb = new CheckBox
                {
                    Content =
                        $"{name} · ${JsonUi.Str(s, "amount_abs")} · {JsonUi.Str(s, "cadence")} " +
                        $"({JsonUi.Int(s, "occurrences", 0)}×)",
                    IsChecked = true,
                    Tag = key,
                };
                cb.Checked += (_, _) =>
                {
                    if (cb.Tag is string k && _recurringItems.TryGetValue(k, out var cur))
                        _recurringItems[k] = (true, cur.Raw);
                };
                cb.Unchecked += (_, _) =>
                {
                    if (cb.Tag is string k && _recurringItems.TryGetValue(k, out var cur))
                        _recurringItems[k] = (false, cur.Raw);
                };
                Fields.Children.Add(cb);
                Fields.Children.Add(new TextBlock
                {
                    Text = JsonUi.Str(s, "reason"),
                    FontSize = 12,
                    Opacity = 0.7,
                    Margin = new Thickness(28, 0, 0, 8),
                    TextWrapping = TextWrapping.Wrap,
                });
            }
        }
        catch (Exception ex)
        {
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private async Task RecurringApplyAndAdvanceAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var accepted = new List<Dictionary<string, object?>>();
        foreach (var kv in _recurringItems)
        {
            if (!kv.Value.Selected) continue;
            var s = kv.Value.Raw;
            accepted.Add(new Dictionary<string, object?>
            {
                ["name"] = JsonUi.Str(s, "name"),
                ["amount_abs"] = JsonUi.Str(s, "amount_abs"),
                ["cadence"] = JsonUi.Str(s, "cadence", "monthly"),
                ["suggested_next_date"] = JsonUi.Str(s, "suggested_next_date"),
                ["selected"] = true,
            });
        }
        if (accepted.Count > 0)
        {
            var res = await api.ApplySetupRecurringAsync(new { accepted });
            InfoBar.Title = "Bills";
            InfoBar.Message = JsonUi.Str(res, "message");
            InfoBar.IsOpen = true;
        }
        var st = await api.SetupAdvanceAsync("next");
        ApplyState(st);
        Render();
    }

    private async Task RenderCategorizeAsync()
    {
        QuestionText.Text = "Categorize spending";
        HintText.Text =
            "We’ll auto-apply high-confidence rules, then ask about your top remaining payees. " +
            "Tap a category chip — creates a rule for next time.";
        NextBtn.Content = "Done categorizing — continue";

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            if (!_categorizeAutoRan)
            {
                var auto = await api.SetupCategorizeAutoAsync(useGrok: false);
                _categorizeAutoRan = true;
                InfoBar.Title = "Auto-categorize";
                InfoBar.Message = JsonUi.Str(auto, "message");
                InfoBar.IsOpen = true;
            }

            var st = await api.GetSetupCategorizeAsync();
            Fields.Children.Add(new TextBlock
            {
                Text = JsonUi.Str(st, "message") + $" ({JsonUi.Str(st, "categorized_pct")}%)",
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });

            _catChips.Clear();
            if (st.TryGetProperty("category_chips", out var chips) && chips.ValueKind == JsonValueKind.Array)
            {
                foreach (var c in chips.EnumerateArray())
                    _catChips.Add((JsonUi.Int(c, "id", 0), JsonUi.Str(c, "name")));
            }

            JsonElement? first = null;
            if (st.TryGetProperty("confirm_queue", out var q) && q.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in q.EnumerateArray())
                {
                    var key = JsonUi.Str(item, "payee_key");
                    if (!string.IsNullOrEmpty(key) && _skippedPayees.Contains(key))
                        continue;
                    first = item;
                    break;
                }
            }

            if (first is null)
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = "Nothing left to confirm — continue to budgets.",
                    TextWrapping = TextWrapping.Wrap,
                });
                _pendingPayeeKey = null;
                return;
            }

            var f = first.Value;
            _pendingPayeeKey = JsonUi.Str(f, "payee_key");
            _pendingPayeeLabel = JsonUi.Str(f, "payee");
            _pendingTxnIds = new List<int>();
            if (f.TryGetProperty("transaction_ids", out var ids) && ids.ValueKind == JsonValueKind.Array)
            {
                foreach (var id in ids.EnumerateArray())
                    if (id.TryGetInt32(out var n)) _pendingTxnIds.Add(n);
            }

            Fields.Children.Add(new TextBlock
            {
                Text =
                    $"Payee: {_pendingPayeeLabel}\n" +
                    $"{JsonUi.Int(f, "count", 0)} transactions · ${JsonUi.Str(f, "total_abs")} total",
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });

            var chipPanel = new StackPanel { Spacing = 6 };
            var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            var col = 0;
            foreach (var (cid, cname) in _catChips.Take(16))
            {
                if (cid <= 0) continue;
                var b = new Button
                {
                    Content = cname,
                    Tag = cid,
                    Padding = new Thickness(10, 6, 10, 6),
                };
                b.Click += async (_, _) =>
                {
                    try { await ConfirmPayeeCategoryAsync(cid); }
                    catch (Exception ex)
                    {
                        ErrorBar.Message = ex.Message;
                        ErrorBar.IsOpen = true;
                    }
                };
                row.Children.Add(b);
                col++;
                if (col >= 4)
                {
                    chipPanel.Children.Add(row);
                    row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
                    col = 0;
                }
            }
            if (row.Children.Count > 0)
                chipPanel.Children.Add(row);
            Fields.Children.Add(chipPanel);

            var skipPayee = new Button
            {
                Content = "Skip this payee",
                Margin = new Thickness(0, 12, 0, 0),
            };
            skipPayee.Click += (_, _) =>
            {
                if (!string.IsNullOrEmpty(_pendingPayeeKey))
                    _skippedPayees.Add(_pendingPayeeKey);
                Render();
            };
            Fields.Children.Add(skipPayee);
        }
        catch (Exception ex)
        {
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private async Task ConfirmPayeeCategoryAsync(int categoryId)
    {
        if (string.IsNullOrEmpty(_pendingPayeeKey) && (_pendingTxnIds is null || _pendingTxnIds.Count == 0))
            return;
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var res = await api.SetupCategorizeConfirmAsync(new
        {
            category_id = categoryId,
            payee_key = _pendingPayeeKey,
            transaction_ids = _pendingTxnIds,
            create_rule = true,
        });
        InfoBar.Title = "Categorized";
        InfoBar.Message =
            $"{JsonUi.Int(res, "updated", 0)} txns → {JsonUi.Str(res, "category_name")}" +
            (JsonUi.Int(res, "rule_id", 0) > 0 ? " · rule saved" : "");
        InfoBar.IsOpen = true;
        _pendingPayeeKey = null;
        _pendingTxnIds = null;
        Render();
    }

    private async Task RenderBudgetsAsync()
    {
        QuestionText.Text = "Budgets from your history";
        HintText.Text =
            "Plans seeded from categorized spend (food→daily, gas→weekly, etc.). " +
            "Edit amounts or leave as-is. Remaining budget reserves out of Safe to spend.";
        NextBtn.Content = "Save budgets & continue";
        _budgetAmountBoxes.Clear();

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var rev = await api.GetSetupBudgetsAsync(seedIfEmpty: true);
            Fields.Children.Add(new TextBlock
            {
                Text = JsonUi.Str(rev, "message"),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });
            if (rev.TryGetProperty("seed", out var seed) && seed.ValueKind == JsonValueKind.Object)
            {
                var msg = JsonUi.Str(seed, "message");
                if (!string.IsNullOrEmpty(msg) && msg != "—")
                    Fields.Children.Add(new TextBlock { Text = msg, Opacity = 0.8, TextWrapping = TextWrapping.Wrap });
            }

            if (!rev.TryGetProperty("rules", out var rules) || rules.ValueKind != JsonValueKind.Array
                || rules.GetArrayLength() == 0)
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = "No budget rules yet — continue and seed later from Budgets.",
                    TextWrapping = TextWrapping.Wrap,
                });
                return;
            }

            foreach (var r in rules.EnumerateArray())
            {
                var id = JsonUi.Int(r, "id", 0);
                var nb = new NumberBox
                {
                    Header = $"{JsonUi.Str(r, "name")} · {JsonUi.Str(r, "period")}",
                    Value = double.TryParse(JsonUi.Str(r, "amount", "0"), out var v) ? v : 0,
                    Minimum = 0,
                    SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact,
                };
                if (id > 0) _budgetAmountBoxes[id] = nb;
                Fields.Children.Add(nb);
            }
        }
        catch (Exception ex)
        {
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private async Task SaveBudgetsAndAdvanceAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        var updates = new List<object>();
        foreach (var kv in _budgetAmountBoxes)
        {
            var val = kv.Value.Value;
            if (double.IsNaN(val)) continue;
            updates.Add(new { id = kv.Key, amount = (decimal)val });
        }
        if (updates.Count > 0)
            await api.ApplySetupBudgetsAsync(new { updates });
        var st = await api.SetupAdvanceAsync("next");
        ApplyState(st);
        Render();
    }

    private async Task RenderBuffersAsync()
    {
        QuestionText.Text = "Safety buffers";
        HintText.Text =
            "Total cash floor never spent. Per-account buffers reserve money inside each checking/savings. " +
            "IFPP uses the larger of total floor vs sum of per-account reserves.";
        NextBtn.Content = "Save & finish setup";
        _acctBufferBoxes.Clear();

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var buf = await api.GetSetupBuffersAsync();
            Fields.Children.Add(new TextBlock
            {
                Text = JsonUi.Str(buf, "message"),
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 8),
            });

            var totalVal = 1000.0;
            double.TryParse(JsonUi.Str(buf, "total_buffer", "1000"), out totalVal);
            _totalBufferBox = new NumberBox
            {
                Header = "Total cash buffer ($)",
                Value = totalVal,
                Minimum = 0,
            };
            Fields.Children.Add(_totalBufferBox);

            if (buf.TryGetProperty("accounts", out var accs) && accs.ValueKind == JsonValueKind.Array)
            {
                Fields.Children.Add(new TextBlock
                {
                    Text = "Per-account buffers",
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Margin = new Thickness(0, 12, 0, 4),
                });
                foreach (var a in accs.EnumerateArray())
                {
                    var id = JsonUi.Int(a, "id", 0);
                    var cur = 0.0;
                    var sb = JsonUi.Str(a, "safety_buffer", "0");
                    if (sb != "—" && sb != "")
                        double.TryParse(sb, out cur);
                    var nb = new NumberBox
                    {
                        Header = $"{JsonUi.Str(a, "nickname")} (bal ${JsonUi.Str(a, "balance")})",
                        Value = cur,
                        Minimum = 0,
                    };
                    if (id > 0) _acctBufferBoxes[id] = nb;
                    Fields.Children.Add(nb);
                }
            }
        }
        catch (Exception ex)
        {
            Fields.Children.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
        }
    }

    private async Task SaveBuffersAndFinishAsync()
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync();
        decimal? total = null;
        if (_totalBufferBox is not null && !double.IsNaN(_totalBufferBox.Value))
            total = (decimal)_totalBufferBox.Value;
        var acctBufs = new List<object>();
        foreach (var kv in _acctBufferBoxes)
        {
            if (double.IsNaN(kv.Value.Value)) continue;
            acctBufs.Add(new { id = kv.Key, safety_buffer = (decimal)kv.Value.Value });
        }
        await api.SaveSetupBuffersAsync(new
        {
            total_buffer = total,
            account_buffers = acctBufs,
        });
        // Mark setup complete
        var st = await api.SetupAdvanceAsync("complete");
        ApplyState(st);
        AppState.ShowSetupNav = false;
        MsgText.Text = "Setup complete — Home is ready with Safe to spend, bills, and budgets.";
        Render();
    }

    private async void Skip_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            // On path phases, skip phase not whole setup when mid-plaid
            if (_phase is "ai_keys" or "plaid_link" or "plaid_keys")
            {
                var st = await api.SetupAdvanceAsync("skip_phase");
                ApplyState(st);
                Render();
                return;
            }
            await api.SetupCompleteAsync("skipped-from-wizard");
            AppState.ShowSetupNav = false;
            Frame?.Navigate(typeof(HomePage));
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }
}
