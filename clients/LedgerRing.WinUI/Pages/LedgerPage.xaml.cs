using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace LedgerRing_WinUI.Pages;

public sealed partial class LedgerPage : Page
{
    private JsonElement _accounts = default;
    private readonly List<CatOpt> _allCats = new();
    private readonly ObservableCollection<TxnRow> _rows = new();
    private bool _loading;

    public LedgerPage()
    {
        InitializeComponent();
        DateBox.Date = DateTimeOffset.Now;
        TxnList.ItemsSource = _rows;
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();
    private async void Filter_Changed(object sender, RoutedEventArgs e) => await LoadTxnsAsync();
    private async void Profile_Changed(object sender, SelectionChangedEventArgs e) => FillAccounts();

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            var profiles = await api.GetProfilesAsync();
            ProfileBox.Items.Clear();
            foreach (var p in profiles.EnumerateArray())
            {
                ProfileBox.Items.Add(new ComboBoxItem
                {
                    Content = JsonUi.Str(p, "display_name"),
                    Tag = p.GetProperty("id").GetInt32(),
                });
            }
            if (ProfileBox.Items.Count > 0) ProfileBox.SelectedIndex = 0;

            _accounts = await api.GetAccountsAsync();
            FillAccounts();

            var cats = await api.GetCategoriesAsync();
            _allCats.Clear();
            _allCats.Add(new CatOpt(null, "(uncategorized)"));
            foreach (var c in cats.EnumerateArray())
            {
                var id = c.GetProperty("id").GetInt32();
                var name = JsonUi.Str(c, "display_name");
                var tax = JsonUi.Str(c, "tax_line", "");
                var label = string.IsNullOrEmpty(tax) || tax == "—" ? name : $"{name} [{tax}]";
                _allCats.Add(new CatOpt(id, label));
            }
            CatBox.Items.Clear();
            foreach (var c in _allCats)
                CatBox.Items.Add(c);
            if (CatBox.Items.Count > 0) CatBox.SelectedIndex = 0;

            await LoadTxnsAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void FillAccounts()
    {
        AccountBox.Items.Clear();
        if (ProfileBox.SelectedItem is not ComboBoxItem pi || pi.Tag is not int profileId) return;
        if (_accounts.ValueKind != JsonValueKind.Array) return;
        foreach (var a in _accounts.EnumerateArray())
        {
            if (a.GetProperty("profile_id").GetInt32() != profileId) continue;
            AccountBox.Items.Add(new ComboBoxItem
            {
                Content = $"{JsonUi.Str(a, "nickname")} [{JsonUi.Str(a, "kind")}]",
                Tag = a.GetProperty("id").GetInt32(),
            });
        }
        if (AccountBox.Items.Count > 0) AccountBox.SelectedIndex = 0;
    }

    private async Task LoadTxnsAsync()
    {
        _loading = true;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var uncat = UncatBox.IsChecked == true;
            var txns = await api.GetTransactionsAsync(120, uncat);
            _rows.Clear();
            foreach (var t in txns.EnumerateArray())
            {
                var id = t.GetProperty("id").GetInt32();
                var payee = JsonUi.Str(t, "payee", "(no payee)");
                var title = $"{JsonUi.Str(t, "txn_date")} · {payee} · {JsonUi.Money(t, "amount")}";
                int? catId = null;
                if (t.TryGetProperty("category_id", out var c) && c.ValueKind != JsonValueKind.Null)
                    catId = c.GetInt32();
                var selected = _allCats.FirstOrDefault(x => x.Id == catId) ?? _allCats[0];
                var cats = new ObservableCollection<CatOpt>(_allCats);
                _rows.Add(new TxnRow(id, title, $"profile {JsonUi.Str(t, "profile_id")} · acct {JsonUi.Str(t, "account_id")}", cats, selected));
            }
            MsgText.Text = $"{_rows.Count} transactions";
        }
        finally
        {
            _loading = false;
        }
    }

    private async void Category_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_loading) return;
        if (sender is not ComboBox box || box.Tag is not int txnId) return;
        if (box.SelectedItem is not CatOpt cat) return;
        try
        {
            using var api = new LedgerApiClient();
            await api.PatchTransactionAsync(txnId, new { category_id = cat.Id }, learn: true);
            MsgText.Text = $"Updated txn #{txnId} → {cat.Label}";
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
            if (ProfileBox.SelectedItem is not ComboBoxItem p || p.Tag is not int profileId)
                throw new InvalidOperationException("Pick entity.");
            if (AccountBox.SelectedItem is not ComboBoxItem a || a.Tag is not int accountId)
                throw new InvalidOperationException("Pick account.");
            var date = DateBox.Date?.Date ?? DateTime.Today;
            int? catId = CatBox.SelectedItem is CatOpt co ? co.Id : null;

            var body = new Dictionary<string, object?>
            {
                ["profile_id"] = profileId,
                ["account_id"] = accountId,
                ["txn_date"] = date.ToString("yyyy-MM-dd"),
                ["amount"] = double.IsNaN(AmtBox.Value) ? 0m : (decimal)AmtBox.Value,
                ["payee"] = string.IsNullOrWhiteSpace(PayeeBox.Text) ? null : PayeeBox.Text.Trim(),
                ["category_id"] = catId,
                ["status"] = "cleared",
                ["is_transfer"] = false,
            };

            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            await api.CreateTransactionAsync(body);
            MsgText.Text = "Transaction added.";
            PayeeBox.Text = "";
            await LoadTxnsAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    public sealed class CatOpt
    {
        public int? Id { get; }
        public string Label { get; }
        public CatOpt(int? id, string label) { Id = id; Label = label; }
        public override string ToString() => Label;
    }

    public sealed class TxnRow : INotifyPropertyChanged
    {
        public int Id { get; }
        public string Title { get; }
        public string Subtitle { get; }
        public ObservableCollection<CatOpt> Categories { get; }
        private CatOpt _selected;
        public CatOpt SelectedCategory
        {
            get => _selected;
            set { _selected = value; OnPropertyChanged(); }
        }

        public TxnRow(int id, string title, string subtitle, ObservableCollection<CatOpt> cats, CatOpt selected)
        {
            Id = id;
            Title = title;
            Subtitle = subtitle;
            Categories = cats;
            _selected = selected;
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        private void OnPropertyChanged([CallerMemberName] string? n = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
    }
}
