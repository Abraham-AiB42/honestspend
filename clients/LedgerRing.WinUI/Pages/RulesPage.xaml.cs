using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class RulesPage : Page
{
    private DispatcherQueueTimer? _testDebounce;
    private int _testSeq;

    public RulesPage()
    {
        InitializeComponent();
        _testDebounce = DispatcherQueue.CreateTimer();
        _testDebounce.Interval = TimeSpan.FromMilliseconds(450);
        _testDebounce.IsRepeating = false;
        _testDebounce.Tick += async (_, _) => await RunTestAsync(silent: true);
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();

    private void Pattern_Changed(object sender, TextChangedEventArgs e) => ScheduleTest();
    private void Match_Changed(object sender, SelectionChangedEventArgs e) => ScheduleTest();
    private async void Test_Click(object sender, RoutedEventArgs e) => await RunTestAsync(silent: false);

    private void ScheduleTest()
    {
        if (_testDebounce is null) return;
        _testDebounce.Stop();
        _testDebounce.Start();
    }

    private string CurrentMatchType()
    {
        if (MatchBox.SelectedItem is ComboBoxItem mi && mi.Tag is string mt)
            return mt;
        return "contains";
    }

    private async Task RunTestAsync(bool silent)
    {
        var pattern = PatternBox.Text?.Trim() ?? "";
        if (string.IsNullOrEmpty(pattern))
        {
            TestSummaryText.Text = "Type a pattern to preview matches against recent payees.";
            TestMatchList.ItemsSource = null;
            return;
        }

        var seq = ++_testSeq;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.TestRuleAsync(CurrentMatchType(), pattern);
            if (seq != _testSeq) return; // stale

            var count = res.TryGetProperty("match_count", out var mc) ? mc.GetInt32() : 0;
            var scanned = res.TryGetProperty("scanned", out var sc) ? sc.GetInt32() : 0;
            var lines = new List<string>();
            if (res.TryGetProperty("matches", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var m in arr.EnumerateArray())
                {
                    var payee = JsonUi.Str(m, "payee", "(no payee)");
                    var date = JsonUi.Str(m, "txn_date", "");
                    var amt = JsonUi.Str(m, "amount", "");
                    lines.Add(string.IsNullOrEmpty(date) ? payee : $"{date} · {payee} · {amt}");
                }
            }
            TestMatchList.ItemsSource = lines;
            TestSummaryText.Text = count == 0
                ? $"No matches in {scanned} recent payees · pattern \"{pattern}\""
                : $"{count} match{(count == 1 ? "" : "es")} of {scanned} recent payees · \"{pattern}\"";
            if (!silent)
                MsgText.Text = TestSummaryText.Text;
        }
        catch (Exception ex)
        {
            if (seq != _testSeq) return;
            TestSummaryText.Text = silent ? "Test paused (engine offline?)." : ex.Message;
            if (!silent)
            {
                ErrorBar.Message = ex.Message;
                ErrorBar.IsOpen = true;
            }
        }
    }

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            try
            {
                var st = await api.GetCategorizerStatusAsync();
                StatusText.Text =
                    $"Categorizer · Grok {(st.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "on" : "off")} · " +
                    JsonUi.Str(st, "message", JsonUi.Str(st, "hint", ""));
            }
            catch
            {
                StatusText.Text = "Rules engine ready.";
            }

            var cats = await api.GetCategoriesAsync();
            CatBox.Items.Clear();
            foreach (var c in cats.EnumerateArray())
            {
                var name = JsonUi.Str(c, "display_name");
                var tax = JsonUi.Str(c, "tax_line", "");
                CatBox.Items.Add(new ComboBoxItem
                {
                    Content = string.IsNullOrEmpty(tax) || tax == "—" ? name : $"{name} [{tax}]",
                    Tag = c.GetProperty("id").GetInt32(),
                });
            }
            if (CatBox.Items.Count > 0) CatBox.SelectedIndex = 0;

            var rules = await api.GetRulesAsync();
            var rows = new List<RuleRow>();
            if (rules.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in rules.EnumerateArray())
                {
                    var id = r.GetProperty("id").GetInt32();
                    var mt = JsonUi.Str(r, "match_type") switch
                    {
                        "contains" => "contains",
                        "starts_with" => "starts with",
                        "exact" => "exact",
                        "regex" => "regex",
                        _ => JsonUi.Str(r, "match_type"),
                    };
                    var title = $"If payee {mt} \"{JsonUi.Str(r, "pattern")}\" → {JsonUi.Str(r, "category_name")}";
                    var src = JsonUi.Str(r, "source") switch
                    {
                        "learned" => "learned from Sort charges",
                        "seed" => "built-in",
                        "user" => "you added",
                        _ => JsonUi.Str(r, "source"),
                    };
                    var sub =
                        $"Priority {JsonUi.Str(r, "priority")} · {src}" +
                        (r.TryGetProperty("is_transfer", out var t) && t.GetBoolean() ? " · transfer" : "");
                    rows.Add(new RuleRow(id, title, sub));
                }
            }
            RuleList.ItemsSource = rows;
            MsgText.Text = $"{rows.Count} rules";
            if (!string.IsNullOrWhiteSpace(PatternBox.Text))
                await RunTestAsync(silent: true);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Add_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (string.IsNullOrWhiteSpace(PatternBox.Text))
                throw new InvalidOperationException("Pattern required.");
            if (CatBox.SelectedItem is not ComboBoxItem ci || ci.Tag is not int catId)
                throw new InvalidOperationException("Pick a category.");
            var match = CurrentMatchType();

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateRuleAsync(new
            {
                match_type = match,
                pattern = PatternBox.Text.Trim(),
                category_id = catId,
                priority = double.IsNaN(PriorityBox.Value) ? 100 : (int)PriorityBox.Value,
                is_transfer = TransferBox.IsChecked == true,
                active = true,
            });
            PatternBox.Text = "";
            TestMatchList.ItemsSource = null;
            TestSummaryText.Text = "Type a pattern to preview matches against recent payees.";
            MsgText.Text = "Rule added.";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Delete_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not int id) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.DeleteRuleAsync(id);
            MsgText.Text = "Deleted rule.";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private sealed record RuleRow(int Id, string Title, string Subtitle);
}
