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
        "plaid", "reconcile", "data", "users", "bills", "credit", "offers", "buy",
        "taxvault", "intermix",
    };

    /// <summary>Visible in Simple mode; everything else is Full books.</summary>
    private static readonly HashSet<string> SimpleNavTags = new(StringComparer.OrdinalIgnoreCase)
    {
        "home", "add", "setup", "buy", "review", "import", "about",
    };

    public MainWindow()
    {
        // ComboBox SelectionChanged fires while XAML loads (IsSelected items) —
        // before ShellModeText / NavFrame exist. Guard handlers until init finishes.
        _shellLoading = true;
        InitializeComponent();

        try
        {
            ExtendsContentIntoTitleBar = true;
            SetTitleBar(AppTitleBar);
            if (AppWindow?.TitleBar is not null)
                AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
            // Prefer absolute path — relative "Assets/..." fails in packaged Store installs
            var iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico");
            if (File.Exists(iconPath))
                AppWindow?.SetIcon(iconPath);
            AppWindow?.Resize(new Windows.Graphics.SizeInt32(1180, 820));
        }
        catch
        {
            /* Title bar chrome is optional if host rejects it */
        }

        try
        {
            var ls = Windows.Storage.ApplicationData.Current.LocalSettings.Values;
            if (ls["UiMode"] is string m && m == "full")
                AppState.SimpleMode = false;
        }
        catch { /* ignore */ }
        ApplyReadOnlyChrome();
        ApplySimpleChrome();
        NavFrame.Navigated += (_, _) =>
        {
            try { AppTitleBar.IsBackButtonVisible = NavFrame.CanGoBack; }
            catch { /* ignore */ }
        };
        _shellLoading = false;
    }

    private async void NavView_Loaded(object sender, RoutedEventArgs e)
    {
        // Store cert: never let Loaded throw — always leave a navigable Home shell.
        try
        {
            await NavView_LoadedCoreAsync();
        }
        catch (Exception ex)
        {
            try
            {
                ShellModeText.Text = "Starting…";
                SelectNav("home");
                NavFrame.Navigate(typeof(HomePage));
            }
            catch { /* ignore */ }
            System.Diagnostics.Debug.WriteLine("NavView_Loaded: " + ex);
        }
    }

    private async Task NavView_LoadedCoreAsync()
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

        // App lock / encryption gate — before books UI
        if (AppLockService.NeedsUnlock)
        {
            NavView.IsEnabled = false;
            NavFrame.Navigate(typeof(LockPage));
            return;
        }

        // First launch: show Get started now. Engine can catch up during steps 1–3.
        if (StorageLocationService.LooksLikeFirstRun() && !AppLockService.NeedsUnlock)
        {
            AppLockService.MarkUnlocked();
            RefreshLockChip();
            await ContinueAfterUnlockAsync();
            return;
        }

        // Encryption may require unlock even if UI lock mode is none (desync / crash recovery)
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var h = await api.GetHealthDetailsAsync();
            var needs = h is JsonElement he
                && he.TryGetProperty("needs_unlock", out var nu)
                && nu.ValueKind == JsonValueKind.True;
            if (needs)
            {
                NavView.IsEnabled = false;
                NavFrame.Navigate(typeof(LockPage));
                return;
            }
        }
        catch { /* continue — offline path below */ }

        AppLockService.MarkUnlocked();
        RefreshLockChip();
        await ContinueAfterUnlockAsync();
    }

    /// <summary>Called from LockPage after successful unlock (books must already be open).</summary>
    public async void OnAppUnlocked()
    {
        NavView.IsEnabled = true;
        RefreshLockChip();
        await ContinueAfterUnlockAsync();
    }

    /// <summary>Re-show lock when API returns 423 (books sealed mid-session).</summary>
    public void ForceLockScreen(string? message = null)
    {
        try
        {
            AppLockService.LockSession();
            NavView.IsEnabled = false;
            NavFrame.Navigate(typeof(LockPage));
        }
        catch { /* ignore */ }
    }

    private async Task ContinueAfterUnlockAsync()
    {
        await LoadShellEntitiesAsync();
        ApplySimpleChrome();

        if (StorageLocationService.LooksLikeFirstRun())
        {
            StorageLocationService.ResetLocalSetupFlags();
            AppState.ShowSetupNav = true;
            ApplySimpleChrome();
            SelectNav("setup");
            NavFrame.Navigate(typeof(FirstRunPage));
            return;
        }

        // Wait for engine + books ready so we don't land on 423 Home
        JsonElement? ob = null;
        for (var attempt = 0; attempt < 12; attempt++)
        {
            try
            {
                using var api = new LedgerApiClient();
                await api.EnsureBackendAsync();
                if (!await api.HealthAsync())
                {
                    await Task.Delay(500);
                    continue;
                }
                var h = await api.GetHealthDetailsAsync();
                if (h is JsonElement he
                    && he.TryGetProperty("needs_unlock", out var nu)
                    && nu.ValueKind == JsonValueKind.True)
                {
                    ForceLockScreen();
                    return;
                }
                if (!await api.BooksReadyAsync())
                {
                    await Task.Delay(400);
                    continue;
                }
                ob = await api.GetOnboardingAsync();
                break;
            }
            catch (Exception ex) when (LedgerApiClient.IsLockedStatusCode(ex))
            {
                ForceLockScreen();
                return;
            }
            catch
            {
                await Task.Delay(500);
            }
        }

        if (ob is JsonElement onboard)
        {
            var needs = onboard.TryGetProperty("needs_setup", out var n) && n.GetBoolean();
            AppState.ShowSetupNav = needs;
            ApplySimpleChrome();
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
        else
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
        // Guest pages (Plaid, tax vault, …) stay in Simple. View toggle is the only persist.
        if (AppState.SimpleMode && !SimpleNavTags.Contains(tag)
            && tag is not ("home" or "settings" or "license"))
        {
            try { ShellModeText.Text = "Opening " + tag + "…"; } catch { /* chrome optional */ }
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
        var inWizard = AppState.ShowSetupNav;
        try
        {
            NavView.IsPaneVisible = !inWizard;
            NavView.IsSettingsVisible = !inWizard;
            NavView.IsPaneToggleButtonVisible = false;
            AppTitleBar.IsPaneToggleButtonVisible = !inWizard;
            if (NavView.Header is UIElement hdr)
                hdr.Visibility = inWizard ? Visibility.Collapsed : Visibility.Visible;
        }
        catch { /* chrome optional during first paint */ }

        foreach (var item in NavView.MenuItems)
        {
            if (item is not NavigationViewItem nvi || nvi.Tag is not string tag)
                continue;
            if (AppState.ReadOnlySession)
                continue; // ApplyReadOnlyChrome owns visibility
            // Get started is the full-window wizard — never a sidebar item
            if (tag == "setup")
            {
                nvi.Visibility = Visibility.Collapsed;
                continue;
            }
            if (AppState.SimpleMode)
                nvi.Visibility = SimpleNavTags.Contains(tag) ? Visibility.Visible : Visibility.Collapsed;
            else
                nvi.Visibility = Visibility.Visible;
        }
        if (!inWizard)
            ApplySimpleShellHeader();
        try
        {
            ShellModeText.Text = AppState.SimpleMode
                ? "Simple · safe to spend first"
                : "Full books · every tool";
        }
        catch { /* header may be collapsed */ }
    }

    /// <summary>Quiet Simple chrome: hide Who / All money / CPA for single-pile households.</summary>
    private void ApplySimpleShellHeader()
    {
        var multiWho = ShellEntityBox.Items.Count > 1;
        if (AppState.SimpleMode && !multiWho)
        {
            WhoLabel.Visibility = Visibility.Collapsed;
            ShellEntityBox.Visibility = Visibility.Collapsed;
            ShellScopeBox.Visibility = Visibility.Collapsed;
            CpaModeBtn.Visibility = Visibility.Collapsed;
        }
        else
        {
            WhoLabel.Visibility = Visibility.Visible;
            ShellEntityBox.Visibility = Visibility.Visible;
            ShellScopeBox.Visibility = Visibility.Visible;
            CpaModeBtn.Visibility = AppState.SimpleMode ? Visibility.Collapsed : Visibility.Visible;
        }
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
            ApplySimpleShellHeader();
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
        if (ShellScopeBox?.SelectedItem is ComboBoxItem sc && sc.Tag is string st)
            AppState.IfppScope = st;
        if (AppState.IfppScope == "entity" && ShellEntityBox?.SelectedItem is ComboBoxItem ei && ei.Tag is int id)
            AppState.SelectedProfileId = id;
        else if (AppState.IfppScope == "group")
            AppState.SelectedProfileId = null;
        if (ShellEntityBox is not null)
            ShellEntityBox.IsEnabled = AppState.IfppScope == "entity" && !AppState.ReadOnlySession;
        if (ShellModeText is null) return;
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

    public async void RefreshLockChip()
    {
        try
        {
            if (LockChipBtn is null) return;
            if (AppLockService.NeedsUnlock)
            {
                LockChipBtn.Content = "Books locked";
                LockChipBtn.Visibility = Visibility.Visible;
                LockChipBtn.IsEnabled = true;
                return;
            }
            var enc = false;
            try
            {
                using var api = new LedgerApiClient();
                if (await api.HealthAsync())
                {
                    var h = await api.GetHealthDetailsAsync();
                    enc = h is JsonElement he
                        && he.TryGetProperty("encryption_enabled", out var e)
                        && e.ValueKind == JsonValueKind.True;
                }
            }
            catch { /* offline */ }

            // Only show seal/lock when lock or encryption actually does something
            if (!AppLockService.IsLockEnabled && !enc)
            {
                LockChipBtn.Content = "Books unlocked";
                LockChipBtn.Visibility = Visibility.Collapsed;
                return;
            }
            LockChipBtn.Visibility = Visibility.Visible;
            LockChipBtn.Content = "Lock books";
            LockChipBtn.IsEnabled = true;
        }
        catch { /* ignore */ }
    }

    private async void LockChip_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (AppLockService.NeedsUnlock)
            {
                ForceLockScreen();
                return;
            }
            if (!AppLockService.IsLockEnabled)
            {
                // Encryption-only: seal without forcing junk PIN on LockPage
                LockChipBtn.IsEnabled = false;
                LockChipBtn.Content = "Locking…";
                await AppLockService.SealDatabaseAsync();
                LockChipBtn.Content = "Locked";
                return;
            }
            LockChipBtn.IsEnabled = false;
            LockChipBtn.Content = "Locking…";
            await AppLockService.SealDatabaseAsync();
            AppLockService.LockSession();
            ForceLockScreen();
        }
        catch (Exception ex)
        {
            try { LockChipBtn.Content = "Lock failed"; } catch { /* ignore */ }
            System.Diagnostics.Debug.WriteLine(ex);
        }
        finally
        {
            try { LockChipBtn.IsEnabled = true; } catch { /* ignore */ }
            RefreshLockChip();
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
        try
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
                case "budgets": NavFrame.Navigate(typeof(BudgetsPage)); break;
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
                case "offers": NavFrame.Navigate(typeof(CreditOffersPage)); break;
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
        catch (Exception ex)
        {
            try
            {
                var dir = WinUiPaths.DefaultLocalDataDir();
                Directory.CreateDirectory(dir);
                File.AppendAllText(
                    Path.Combine(dir, "winui-crash.log"),
                    $"[{DateTime.Now:O}] [NavigateTag:{tag}] {ex}\n\n");
            }
            catch { /* ignore */ }
            // Keep shell alive — fall back to Home if target page blew up during load
            if (tag != "home")
            {
                try { NavFrame.Navigate(typeof(HomePage)); } catch { /* ignore */ }
            }
        }
    }
}
