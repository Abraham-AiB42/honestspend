using System.Collections.ObjectModel;
using System.Text.Json;
using Floatpile_WinUI.Helpers;
using Floatpile_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace Floatpile_WinUI.Pages;

/// <summary>Simple-mode review: sort a few charges — no ledger chrome or raw IDs.</summary>
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
        StatusText.Text = "Looking at uncategorized charges…";
        EmptyTitle.Visibility = Visibility.Collapsed;
        EmptyHint.Visibility = Visibility.Collapsed;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.CategorizeBatchAsync(apply, GrokBox.IsChecked == true, 40);
            _rows.Clear();
            if (!res.TryGetProperty("results", out var results) || results.ValueKind != JsonValueKind.Array)
            {
                ShowEmpty("All clear", "Nothing waiting. Safe to close.");
                return;
            }

            var pending = 0;
            var applied = 0;
            foreach (var r in results.EnumerateArray())
            {
                var tid = r.GetProperty("transaction_id").GetInt32();
                var payee = JsonUi.Str(r, "payee", "Charge");
                var when = JsonUi.Str(r, "txn_date", "");
                var amt = JsonUi.Money(r, "amount");
                var title = string.IsNullOrEmpty(when) || when == "—"
                    ? $"{payee} · {amt}"
                    : $"{payee} · {amt} · {when}";

                var sug = r.GetProperty("suggestion");
                var name = JsonUi.Str(sug, "category_name", "Uncategorized");
                var conf = sug.TryGetProperty("confidence", out var c) ? c.GetDouble() : 0;
                var confPct = conf > 0 ? $" · {conf * 100:0}% sure" : "";
                var reason = JsonUi.Str(sug, "reason", "");
                if (string.IsNullOrEmpty(reason) || reason == "—")
                    reason = "Based on your rules and past accepts.";
                var isApplied = r.TryGetProperty("applied", out var ap) && ap.GetBoolean();
                if (isApplied) applied++;
                else pending++;

                int? catId = null;
                if (sug.TryGetProperty("category_id", out var cid) && cid.ValueKind != JsonValueKind.Null)
                    catId = cid.GetInt32();

                _rows.Add(new ReviewRow(
                    tid,
                    title,
                    $"Category: {name}{confPct}",
                    reason,
                    catId,
                    isApplied || catId is null ? Visibility.Collapsed : Visibility.Visible,
                    isApplied ? "done" : ""));
            }

            if (_rows.Count == 0)
            {
                ShowEmpty("All clear", "Nothing waiting. Come back after imports or bank sync.");
                return;
            }

            StatusText.Text = apply
                ? $"Filed {applied} of {_rows.Count} automatically. {pending} still need a glance."
                : pending == 0
                    ? "Queue handled — nice."
                    : $"{pending} charge{(pending == 1 ? "" : "s")} to confirm.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
            StatusText.Text = "Could not load queue.";
        }
    }

    private void ShowEmpty(string title, string hint)
    {
        EmptyTitle.Text = title;
        EmptyHint.Text = hint;
        EmptyTitle.Visibility = Visibility.Visible;
        EmptyHint.Visibility = Visibility.Visible;
        StatusText.Text = "";
        _rows.Clear();
    }

    private async void Accept_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ReviewRow row) return;
        if (row.CategoryId is null) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.PatchTransactionAsync(row.TxnId, new { category_id = row.CategoryId.Value }, learn: true);
            await RunBatchAsync(false);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void Skip_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not ReviewRow row) return;
        _rows.Remove(row);
        var left = _rows.Count(r => r.AcceptVisible == Visibility.Visible);
        StatusText.Text = left == 0
            ? "Queue cleared for now (skipped rest)."
            : $"{left} left · skipped one.";
        if (_rows.Count == 0)
            ShowEmpty("All clear", "Skipped for this session. Refresh to see again.");
    }

    private async void AcceptAll_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        var pending = _rows.Where(r => r.CategoryId is not null && r.AcceptVisible == Visibility.Visible).ToList();
        if (pending.Count == 0)
        {
            StatusText.Text = "Nothing to accept — refresh or use Accept confident ones.";
            return;
        }
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var n = 0;
            for (var i = 0; i < pending.Count; i++)
            {
                var row = pending[i];
                await api.PatchTransactionAsync(
                    row.TxnId,
                    new { category_id = row.CategoryId!.Value },
                    learn: true);
                n++;
            }
            StatusText.Text = $"Accepted {n} · rules learned.";
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
