using System.Collections.ObjectModel;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class ReviewPage : Page
{
    private readonly ObservableCollection<ReviewRow> _rows = new();

    public ReviewPage()
    {
        InitializeComponent();
        ReviewList.ItemsSource = _rows;
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await RunBatchAsync(apply: false);
    }

    private async void Suggest_Click(object sender, RoutedEventArgs e) => await RunBatchAsync(false);
    private async void ApplyHigh_Click(object sender, RoutedEventArgs e) => await RunBatchAsync(true);

    private async Task RunBatchAsync(bool apply)
    {
        ErrorBar.IsOpen = false;
        StatusText.Text = "Working…";
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CategorizeBatchAsync(apply, GrokBox.IsChecked == true, 80);
            _rows.Clear();
            if (!res.TryGetProperty("results", out var results) || results.ValueKind != JsonValueKind.Array)
            {
                StatusText.Text = "No results.";
                return;
            }
            var applied = 0;
            foreach (var r in results.EnumerateArray())
            {
                var tid = r.GetProperty("transaction_id").GetInt32();
                var title = $"{JsonUi.Str(r, "txn_date")} · {JsonUi.Str(r, "payee", "(no payee)")} · {JsonUi.Money(r, "amount")}";
                var sug = r.GetProperty("suggestion");
                var name = JsonUi.Str(sug, "category_name", "—");
                var conf = sug.TryGetProperty("confidence", out var c) ? c.GetDouble() : 0;
                var source = JsonUi.Str(sug, "source");
                var reason = JsonUi.Str(sug, "reason");
                var isApplied = r.TryGetProperty("applied", out var ap) && ap.GetBoolean();
                if (isApplied) applied++;
                int? catId = null;
                if (sug.TryGetProperty("category_id", out var cid) && cid.ValueKind != JsonValueKind.Null)
                    catId = cid.GetInt32();
                _rows.Add(new ReviewRow(
                    tid,
                    title,
                    $"Suggest: {name} · {conf * 100:0}% · {source}",
                    reason,
                    catId,
                    isApplied ? Visibility.Collapsed : Visibility.Visible,
                    isApplied ? "applied" : ""));
            }
            StatusText.Text = apply
                ? $"Auto-applied {applied} of {_rows.Count}."
                : $"{_rows.Count} suggestions. Grok: {(res.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "on" : "off")}.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            StatusText.Text = "Error";
        }
    }

    private async void Accept_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ReviewRow row) return;
        if (row.CategoryId is null) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.PatchTransactionAsync(row.TxnId, new { category_id = row.CategoryId.Value }, learn: true);
            row.AcceptVisible = Visibility.Collapsed;
            row.Badge = "accepted";
            // force refresh list item — simplest: reload
            await RunBatchAsync(false);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    public sealed class ReviewRow
    {
        public int TxnId { get; }
        public string Title { get; }
        public string Suggestion { get; }
        public string Reason { get; }
        public int? CategoryId { get; }
        public Visibility AcceptVisible { get; set; }
        public string Badge { get; set; }

        public ReviewRow(int txnId, string title, string suggestion, string reason, int? categoryId, Visibility acceptVisible, string badge)
        {
            TxnId = txnId;
            Title = title;
            Suggestion = suggestion;
            Reason = reason;
            CategoryId = categoryId;
            AcceptVisible = acceptVisible;
            Badge = badge;
        }
    }
}
