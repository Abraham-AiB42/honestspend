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
