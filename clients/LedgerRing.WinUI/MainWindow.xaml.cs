using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Pages;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LedgerRing_WinUI;

public sealed partial class MainWindow : Window
{
    private bool _shellLoading;
    private static readonly HashSet<string> WriteNavTags = new(StringComparer.OrdinalIgnoreCase)
    {
        "setup", "entities", "accounts", "ledger", "review", "rules", "import",
        "plaid", "reconcile", "data", "users", "bills", "credit", "buy",
        "taxvault", "intermix",
    };

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.SetIcon("Assets/AppIcon.ico");
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1180, 820));
        ApplyReadOnlyChrome();
    }

    private async void NavView_Loaded(object sender, RoutedEventArgs e)
    {
        await LoadShellEntitiesAsync();

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var ob = await api.GetOnboardingAsync();
            var needs = ob.TryGetProperty("needs_setup", out var n) && n.GetBoolean();
            if (needs && !AppState.ReadOnlySession)
            {
                SelectNav("setup");
                NavFrame.Navigate(typeof(SetupPage));
                return;
            }
        }
        catch
        {
            // Engine offline — still show home; Settings can start it.
        }

        SelectNav("home");
        NavFrame.Navigate(typeof(HomePage));
    }

    private async Task LoadShellEntitiesAsync()
    {
        _shellLoading = true;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var profiles = await api.GetProfilesAsync();
            ShellEntityBox.Items.Clear();
            var idx = 0;
            var i = 0;
            foreach (var p in profiles.EnumerateArray())
            {
                var id = p.GetProperty("id").GetInt32();
                ShellEntityBox.Items.Add(new ComboBoxItem
                {
                    Content = $"{JsonUi.Str(p, "display_name")} ({JsonUi.Str(p, "entity_type")})",
                    Tag = id,
                });
                if (AppState.SelectedProfileId == id) idx = i;
                i++;
            }
            if (ShellEntityBox.Items.Count > 0)
                ShellEntityBox.SelectedIndex = AppState.SelectedProfileId is null ? 0 : idx;

            for (var j = 0; j < ShellScopeBox.Items.Count; j++)
            {
                if (ShellScopeBox.Items[j] is ComboBoxItem cbi && cbi.Tag as string == AppState.IfppScope)
                {
                    ShellScopeBox.SelectedIndex = j;
                    break;
                }
            }
            ShellEntityBox.IsEnabled = AppState.IfppScope == "entity";
            ApplyScopeFromShell();
        }
        catch
        {
            ShellModeText.Text = "Engine offline — open Settings to start.";
        }
        finally
        {
            _shellLoading = false;
        }
    }

    private void ShellScope_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_shellLoading) return;
        ApplyScopeFromShell();
        AppState.NotifyScopeChanged();
        // Re-navigate current page so lists re-filter
        if (NavView.SelectedItem is NavigationViewItem item && item.Tag is string tag)
            NavigateTag(tag);
    }

    private void ApplyScopeFromShell()
    {
        if (ShellScopeBox.SelectedItem is ComboBoxItem sc && sc.Tag is string st)
            AppState.IfppScope = st;
        if (AppState.IfppScope == "entity" && ShellEntityBox.SelectedItem is ComboBoxItem ei && ei.Tag is int id)
            AppState.SelectedProfileId = id;
        else if (AppState.IfppScope == "group")
            AppState.SelectedProfileId = null;
        ShellEntityBox.IsEnabled = AppState.IfppScope == "entity" && !AppState.ReadOnlySession;
        ShellModeText.Text = AppState.ReadOnlySession
            ? "CPA / read-only session — write pages hidden"
            : $"{AppState.IfppScope}" + (AppState.SelectedProfileId is int p ? $" · #{p}" : "");
    }

    private void CpaMode_Click(object sender, RoutedEventArgs e)
    {
        AppState.ReadOnlySession = !AppState.ReadOnlySession;
        CpaModeBtn.Content = AppState.ReadOnlySession ? "Exit CPA mode" : "CPA mode";
        ApplyReadOnlyChrome();
        ApplyScopeFromShell();
        if (AppState.ReadOnlySession)
        {
            SelectNav("home");
            NavFrame.Navigate(typeof(HomePage));
        }
    }

    private void ApplyReadOnlyChrome()
    {
        foreach (var item in NavView.MenuItems)
        {
            if (item is NavigationViewItem nvi && nvi.Tag is string tag)
            {
                // Tax packet stays available for CPA export
                var hide = AppState.ReadOnlySession && WriteNavTags.Contains(tag) && tag != "tax";
                nvi.Visibility = hide ? Visibility.Collapsed : Visibility.Visible;
            }
        }
    }

    private void SelectNav(string tag)
    {
        foreach (var item in NavView.MenuItems)
        {
            if (item is NavigationViewItem nvi && nvi.Tag as string == tag)
            {
                NavView.SelectedItem = nvi;
                return;
            }
        }
    }

    private void TitleBar_PaneToggleRequested(TitleBar sender, object args)
    {
        NavView.IsPaneOpen = !NavView.IsPaneOpen;
    }

    private void TitleBar_BackRequested(TitleBar sender, object args)
    {
        if (NavFrame.CanGoBack)
            NavFrame.GoBack();
    }

    private void NavView_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected)
        {
            if (AppState.ReadOnlySession)
            {
                // Settings still useful for API key in CPA mode
            }
            NavFrame.Navigate(typeof(SettingsPage));
            return;
        }

        if (args.SelectedItem is NavigationViewItem item && item.Tag is string tag)
            NavigateTag(tag);
    }

    private void NavigateTag(string tag)
    {
        if (AppState.ReadOnlySession && WriteNavTags.Contains(tag) && tag is not ("tax" or "home" or "about" or "buy"))
        {
            SelectNav("home");
            NavFrame.Navigate(typeof(HomePage));
            return;
        }

        switch (tag)
        {
            case "home": NavFrame.Navigate(typeof(HomePage)); break;
            case "setup": NavFrame.Navigate(typeof(SetupPage)); break;
            case "entities": NavFrame.Navigate(typeof(EntitiesPage)); break;
            case "accounts": NavFrame.Navigate(typeof(AccountsPage)); break;
            case "ledger": NavFrame.Navigate(typeof(LedgerPage)); break;
            case "review": NavFrame.Navigate(typeof(ReviewPage)); break;
            case "rules": NavFrame.Navigate(typeof(RulesPage)); break;
            case "import": NavFrame.Navigate(typeof(ImportPage)); break;
            case "plaid": NavFrame.Navigate(typeof(PlaidPage)); break;
            case "reconcile": NavFrame.Navigate(typeof(ReconcilePage)); break;
            case "data": NavFrame.Navigate(typeof(DataPage)); break;
            case "users": NavFrame.Navigate(typeof(UsersPage)); break;
            case "bills": NavFrame.Navigate(typeof(BillsPage)); break;
            case "credit": NavFrame.Navigate(typeof(CreditPage)); break;
            case "buy": NavFrame.Navigate(typeof(BuyPage)); break;
            case "taxvault": NavFrame.Navigate(typeof(TaxVaultPage)); break;
            case "tax": NavFrame.Navigate(typeof(TaxPage)); break;
            case "intermix": NavFrame.Navigate(typeof(IntermixPage)); break;
            case "about": NavFrame.Navigate(typeof(AboutPage)); break;
        }
    }
}
