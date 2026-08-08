using LedgerRing_WinUI.Pages;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LedgerRing_WinUI;

public sealed partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.SetIcon("Assets/AppIcon.ico");
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1180, 820));
    }

    private async void NavView_Loaded(object sender, RoutedEventArgs e)
    {
        // First-run: send empty ledgers to Setup; otherwise Spendable.
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var ob = await api.GetOnboardingAsync();
            var needs = ob.TryGetProperty("needs_setup", out var n) && n.GetBoolean();
            if (needs)
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
            NavFrame.Navigate(typeof(SettingsPage));
            return;
        }

        if (args.SelectedItem is NavigationViewItem item)
        {
            switch (item.Tag as string)
            {
                case "home":
                    NavFrame.Navigate(typeof(HomePage));
                    break;
                case "setup":
                    NavFrame.Navigate(typeof(SetupPage));
                    break;
                case "accounts":
                    NavFrame.Navigate(typeof(AccountsPage));
                    break;
                case "ledger":
                    NavFrame.Navigate(typeof(LedgerPage));
                    break;
                case "review":
                    NavFrame.Navigate(typeof(ReviewPage));
                    break;
                case "rules":
                    NavFrame.Navigate(typeof(RulesPage));
                    break;
                case "import":
                    NavFrame.Navigate(typeof(ImportPage));
                    break;
                case "plaid":
                    NavFrame.Navigate(typeof(PlaidPage));
                    break;
                case "reconcile":
                    NavFrame.Navigate(typeof(ReconcilePage));
                    break;
                case "data":
                    NavFrame.Navigate(typeof(DataPage));
                    break;
                case "users":
                    NavFrame.Navigate(typeof(UsersPage));
                    break;
                case "bills":
                    NavFrame.Navigate(typeof(BillsPage));
                    break;
                case "credit":
                    NavFrame.Navigate(typeof(CreditPage));
                    break;
                case "buy":
                    NavFrame.Navigate(typeof(BuyPage));
                    break;
                case "taxvault":
                    NavFrame.Navigate(typeof(TaxVaultPage));
                    break;
                case "tax":
                    NavFrame.Navigate(typeof(TaxPage));
                    break;
                case "intermix":
                    NavFrame.Navigate(typeof(IntermixPage));
                    break;
                case "about":
                    NavFrame.Navigate(typeof(AboutPage));
                    break;
            }
        }
    }
}
