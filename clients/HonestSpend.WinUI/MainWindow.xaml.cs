using System.Text.Json;
using HonestSpend_WinUI.Helpers;
using HonestSpend_WinUI.Pages;
using HonestSpend_WinUI.Services;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace HonestSpend_WinUI;

public sealed partial class MainWindow : Window
{
    private bool _shellLoading;
    private static readonly HashSet<string> WriteNavTags = new(StringComparer.OrdinalIgnoreCase)
    {
        "setup", "add", "entities", "accounts", "ledger", "review", "rules", "import",
        "plaid", "reconcile", "data", "users", "bills", "credit", "buy",
        "taxvault", "intermix",
    };

    /// <summary>Visible in Simple mode; everything else is Full books.</summary>
    private static readonly HashSet<string> SimpleNavTags = new(StringComparer.OrdinalIgnoreCase)
    {
        "home", "add", "setup", "buy", "review", "about", "license",
    };

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.SetIcon("Assets/AppIcon.ico");
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1180, 820));
        try
        {
            var ls = Windows.Storage.ApplicationData.Current.LocalSettings.Values;
            if (ls["UiMode"] is string m && m == "full")
                AppState.SimpleMode = false;
        }
        catch { /* ignore */ }
        ApplyReadOnlyChrome();
        ApplySimpleChrome();
    }

    private async void NavView_Loaded(object sender, RoutedEventArgs e)
    {
        _shellLoading = true;
        for (var j = 0; j < UiModeBox.Items.Count; j++)
        {
            if (UiModeBox.Items[j] is ComboBoxItem cbi && cbi.Tag as string == (AppState.SimpleMode ? "simple" : "full"))
            {
                UiModeBox.SelectedIndex = j;
                break;
            }
        }
        _shellLoading = false;

        await LoadShellEntitiesAsync();
        ApplySimpleChrome();

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var ob = await api.GetOnboardingAsync();
            var needs = ob.TryGetProperty("needs_setup", out var n) && n.GetBoolean();
            AppState.ShowSetupNav = needs;
            ApplySimpleChrome();
            // Deep-link overrides first-run only when setup is already done
            var deep = WinUiPaths.ConsumeNavigateRequest() ?? AppConfig.OpenPage;
            if (!string.IsNullOrWhiteSpace(deep) && !(needs && !AppState.ReadOnlySession))
            {
                NavigatePublic(deep!);
                return;
            }
            if (needs && !AppState.ReadOnlySession)
            {
                SelectNav("setup");
                NavFrame.Navigate(typeof(FirstRunPage));
                return;
            }
        }
        catch
        {
            // Engine offline — still show home; Settings can start it.
            var deepOffline = WinUiPaths.ConsumeNavigateRequest() ?? AppConfig.OpenPage;
            if (!string.IsNullOrWhiteSpace(deepOffline))
            {
                NavigatePublic(deepOffline!);
                return;
            }
        }

        SelectNav("home");
        NavFrame.Navigate(typeof(HomePage));
    }

    /// <summary>Tray / second-instance deep-link after show.</summary>
    public void ConsumePendingNavigation()
    {
        var tag = WinUiPaths.ConsumeNavigateRequest();
        if (string.IsNullOrWhiteSpace(tag)) return;
        NavigatePublic(tag);
    }

    /// <summary>Public nav from deep-link tags (review, reports, settings, …).</summary>
    public void NavigatePublic(string tag)
    {
        tag = (tag ?? "").Trim().ToLowerInvariant();
        if (tag is "sort" or "charges" or "sort-charges") tag = "review";
        if (tag == "settings")
        {
            // Settings is the NavigationView footer item, not a menu tag
            try
            {
                NavView.SelectedItem = NavView.SettingsItem;
            }
            catch { /* ignore */ }
            NavFrame.Navigate(typeof(SettingsPage));
            // Full books if needed for other pages — settings always available
            return;
        }
        // Deep-link to Full books pages should leave Simple mode
        if (AppState.SimpleMode && !SimpleNavTags.Contains(tag) && tag != "home")
        {
            AppState.SimpleMode = false;
            try
            {
                Windows.Storage.ApplicationData.Current.LocalSettings.Values["UiMode"] = "full";
            }
            catch { /* ignore */ }
            ApplySimpleChrome();
            for (var j = 0; j < UiModeBox.Items.Count; j++)
            {
                if (UiModeBox.Items[j] is ComboBoxItem cbi && cbi.Tag as string == "full")
                {
                    _shellLoading = true;
                    UiModeBox.SelectedIndex = j;
                    _shellLoading = false;
                    break;
                }
            }
        }
        SelectNav(tag);
        NavigateTag(tag);
    }

    private void UiMode_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (_shellLoading) return;
        if (UiModeBox.SelectedItem is ComboBoxItem mi && mi.Tag is string t)
            AppState.SimpleMode = t == "simple";
        try
        {
            Windows.Storage.ApplicationData.Current.LocalSettings.Values["UiMode"] =
                AppState.SimpleMode ? "simple" : "full";
        }
        catch { /* ignore */ }
        ApplySimpleChrome();
        AppState.NotifyModeChanged();
        SelectNav("home");
        NavFrame.Navigate(typeof(HomePage));
    }

    private void ApplySimpleChrome()
    {
        foreach (var item in NavView.MenuItems)
        {
            if (item is not NavigationViewItem nvi || nvi.Tag is not string tag)
                continue;
            if (AppState.ReadOnlySession)
                continue; // ApplyReadOnlyChrome owns visibility
            if (AppState.SimpleMode)
            {
                var show = SimpleNavTags.Contains(tag);
                if (tag == "setup" && !AppState.ShowSetupNav)
                    show = false;
                nvi.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
            }
            else
            {
                // Full books: hide Get started once accounts exist
                if (tag == "setup" && !AppState.ShowSetupNav)
                    nvi.Visibility = Visibility.Collapsed;
                else
                    nvi.Visibility = Visibility.Visible;
            }
        }
        ShellModeText.Text = AppState.SimpleMode
            ? "Simple · safe to spend first"
            : "Full books · every tool";
    }

    /// <summary>Called from Home after setup completes so Get started disappears.</summary>
    public void RefreshSimpleChrome() => ApplySimpleChrome();

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
                var et = UiCopy.EntityType(JsonUi.Str(p, "entity_type"));
                var name = JsonUi.Str(p, "display_name");
                ShellEntityBox.Items.Add(new ComboBoxItem
                {
                    Content = et == "Personal" || name.Equals(et, StringComparison.OrdinalIgnoreCase)
                        ? name
                        : $"{name} · {et}",
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
        if (AppState.ReadOnlySession)
            ShellModeText.Text = "CPA view — read only";
        else if (AppState.SimpleMode)
            ShellModeText.Text = "Simple · safe to spend first";
        else
            ShellModeText.Text = $"Full books · {UiCopy.MoneyView(AppState.IfppScope)}";
    }

    private void CpaMode_Click(object sender, RoutedEventArgs e)
    {
        AppState.ReadOnlySession = !AppState.ReadOnlySession;
        CpaModeBtn.Content = AppState.ReadOnlySession ? "Exit CPA mode" : "CPA mode";
        ApplyReadOnlyChrome();
        if (!AppState.ReadOnlySession)
            ApplySimpleChrome();
        ApplyScopeFromShell();
        if (AppState.ReadOnlySession)
        {
            SelectNav("home");
            NavFrame.Navigate(typeof(HomePage));
        }
    }

    private void ApplyReadOnlyChrome()
    {
        if (!AppState.ReadOnlySession)
            return;
        foreach (var item in NavView.MenuItems)
        {
            if (item is NavigationViewItem nvi && nvi.Tag is string tag)
            {
                var hide = WriteNavTags.Contains(tag) && tag is not ("tax" or "home" or "buy" or "about" or "license");
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
        if (AppState.ReadOnlySession && WriteNavTags.Contains(tag) && tag is not ("tax" or "home" or "about" or "buy" or "license"))
        {
            SelectNav("home");
            NavFrame.Navigate(typeof(HomePage));
            return;
        }

        switch (tag)
        {
            case "home": NavFrame.Navigate(typeof(HomePage)); break;
            case "add": NavFrame.Navigate(typeof(AddHubPage)); break;
            case "setup": NavFrame.Navigate(typeof(FirstRunPage)); break;
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
            case "audit": NavFrame.Navigate(typeof(AuditPage)); break;
            case "bills": NavFrame.Navigate(typeof(BillsPage)); break;
            case "credit": NavFrame.Navigate(typeof(CreditPage)); break;
            case "buy": NavFrame.Navigate(typeof(BuyPage)); break;
            case "scenarios": NavFrame.Navigate(typeof(ScenariosPage)); break;
            case "taxvault": NavFrame.Navigate(typeof(TaxVaultPage)); break;
            case "tax": NavFrame.Navigate(typeof(TaxPage)); break;
            case "reports": NavFrame.Navigate(typeof(ReportsPage)); break;
            case "intermix": NavFrame.Navigate(typeof(IntermixPage)); break;
            case "license": NavFrame.Navigate(typeof(LicensePage)); break;
            case "about": NavFrame.Navigate(typeof(AboutPage)); break;
        }
    }
}
