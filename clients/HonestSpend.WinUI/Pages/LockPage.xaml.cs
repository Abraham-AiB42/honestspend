using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class LockPage : Page
{
    public LockPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        ErrorText.Text = "";
        var mode = AppLockService.Mode;
        HintText.Text = mode switch
        {
            AppLockService.LockMode.Pin => "Enter your 4–8 digit PIN.",
            AppLockService.LockMode.Password => "Enter your app password.",
            AppLockService.LockMode.Platform => "Use Windows Hello, or set a PIN in Settings if Hello fails.",
            _ => "Unlocked.",
        };
        SecretBox.Visibility = mode is AppLockService.LockMode.Pin or AppLockService.LockMode.Password
            ? Visibility.Visible
            : Visibility.Collapsed;
        UnlockBtn.Visibility = mode is AppLockService.LockMode.Pin or AppLockService.LockMode.Password
            ? Visibility.Visible
            : Visibility.Collapsed;
        HelloBtn.Visibility = mode == AppLockService.LockMode.Platform
            ? Visibility.Visible
            : Visibility.Collapsed;

        if (mode == AppLockService.LockMode.Platform)
        {
            var ok = await AppLockService.TryWindowsHelloAsync();
            if (ok)
                FinishUnlocked();
            else
                ErrorText.Text = "Windows Hello cancelled or unavailable. Try again, or clear lock in Settings after OS login.";
        }
    }

    private void Unlock_Click(object sender, RoutedEventArgs e)
    {
        ErrorText.Text = "";
        if (AppLockService.VerifyPinOrPassword(SecretBox.Password))
            FinishUnlocked();
        else
            ErrorText.Text = "Incorrect PIN or password.";
    }

    private async void Hello_Click(object sender, RoutedEventArgs e)
    {
        ErrorText.Text = "";
        var ok = await AppLockService.TryWindowsHelloAsync();
        if (ok)
            FinishUnlocked();
        else
            ErrorText.Text = "Windows Hello did not verify.";
    }

    private void FinishUnlocked()
    {
        if (App.MainWindowInstance is MainWindow mw)
            mw.OnAppUnlocked();
    }
}
