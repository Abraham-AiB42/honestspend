using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace HonestSpend_WinUI.Pages;

public sealed partial class AddHubPage : Page
{
    public AddHubPage()
    {
        InitializeComponent();
    }

    private void Kind_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button b || b.Tag is not string kind) return;
        Frame?.Navigate(typeof(MoneyWizardPage), kind);
    }

    private void Bank_Click(object sender, RoutedEventArgs e)
        => Frame?.Navigate(typeof(PlaidPage));

    private void Import_Click(object sender, RoutedEventArgs e)
        => Frame?.Navigate(typeof(ImportPage));

    private void Playbooks_Click(object sender, RoutedEventArgs e)
        => Frame?.Navigate(typeof(PlaybooksPage));
}
