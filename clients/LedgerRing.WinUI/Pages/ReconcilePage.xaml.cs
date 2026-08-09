using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class ReconcilePage : Page
{
    public ReconcilePage()
    {
        InitializeComponent();
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
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var rep = await api.GetReconcileAsync(AppState.SelectedProfileId);
            SummaryText.Text =
                $"{JsonUi.Str(rep, "count")} accounts · {JsonUi.Str(rep, "drifted")} with drift · " +
                JsonUi.Str(rep, "principle");
            var rows = new List<Row>();
            if (rep.TryGetProperty("accounts", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in arr.EnumerateArray())
                {
                    var id = a.GetProperty("account_id").GetInt32();
                    var title = $"{JsonUi.Str(a, "nickname")} · {JsonUi.Str(a, "kind")} · {JsonUi.Str(a, "status")}";
                    var sub =
                        $"{JsonUi.Str(a, "profile_name")} · books {JsonUi.Money(a, "books_balance")} · " +
                        $"bank {JsonUi.Money(a, "institution_balance")} · drift {JsonUi.Str(a, "drift", "—")} · " +
                        $"reconciled {JsonUi.Str(a, "last_reconciled_at", "never")}";
                    rows.Add(new Row(id, title, sub));
                }
            }
            AcctList.ItemsSource = rows;
            MsgText.Text = rows.Count == 0 ? "No accounts." : $"{rows.Count} listed.";

            try
            {
                var pays = await api.GetPaymentCandidatesAsync(14);
                var plines = new List<string>();
                if (pays.TryGetProperty("candidates", out var pc) && pc.ValueKind == JsonValueKind.Array)
                {
                    foreach (var c in pc.EnumerateArray())
                    {
                        plines.Add(
                            $"{JsonUi.Str(c, "kind")} · ${JsonUi.Str(c, "amount")} · " +
                            $"cash #{JsonUi.Str(c, "cash_txn_id")} ({JsonUi.Str(c, "cash_account")}) → " +
                            $"card #{JsonUi.Str(c, "card_txn_id", "—")} · {JsonUi.Str(c, "suggestion")}");
                        if (double.IsNaN(CashTxnBox.Value) && c.TryGetProperty("cash_txn_id", out var ct))
                            CashTxnBox.Value = ct.GetInt32();
                        if (double.IsNaN(CardTxnBox.Value) && c.TryGetProperty("card_txn_id", out var kt)
                            && kt.ValueKind == JsonValueKind.Number)
                            CardTxnBox.Value = kt.GetInt32();
                    }
                }
                PayList.ItemsSource = plines.Count > 0 ? plines : new List<string> { "No payment matches." };
                PayMsg.Text = JsonUi.Str(pays, "principle");
            }
            catch
            {
                PayList.ItemsSource = new List<string> { "Payment matcher unavailable." };
            }
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void ConfirmPay_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            if (double.IsNaN(CashTxnBox.Value) || double.IsNaN(CardTxnBox.Value))
                throw new InvalidOperationException("Enter cash and card transaction ids.");
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ConfirmPaymentAsync((int)CashTxnBox.Value, (int)CardTxnBox.Value);
            PayMsg.Text = $"Confirmed transfer · ${JsonUi.Str(res, "amount")}";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void TrustBank_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not Row row) return;
        await TrustAsync(row.Id, "institution");
    }

    private async void TrustBooks_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not Row row) return;
        await TrustAsync(row.Id, "books");
    }

    private async Task TrustAsync(int id, string trust)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.ReconcileTrustAsync(id, trust);
            MsgText.Text = $"Trusted {trust} for #{id} · books {JsonUi.Money(res, "books_balance")}";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void SetInst_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not Row row) return;
        var box = new NumberBox { Header = "Institution / statement balance", Minimum = -1e9 };
        var dlg = new ContentDialog
        {
            Title = $"Bank balance · {row.Title}",
            Content = box,
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        try
        {
            var bal = double.IsNaN(box.Value) ? 0m : (decimal)box.Value;
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.SetInstitutionBalanceAsync(row.Id, bal, markReconciled: false);
            MsgText.Text = $"Set institution balance for #{row.Id}.";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private sealed record Row(int Id, string Title, string Subtitle);
}
