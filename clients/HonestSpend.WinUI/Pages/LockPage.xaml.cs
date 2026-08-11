using HonestSpend_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace HonestSpend_WinUI.Pages;

public sealed partial class LockPage : Page
{
    private bool _busy;

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
            AppLockService.LockMode.Pin =>
                "Enter your PIN. This also unlocks your encrypted books.",
            AppLockService.LockMode.Password =>
                "Enter your password. This also unlocks your encrypted books.",
            AppLockService.LockMode.Platform =>
                "Use Windows Hello to unlock the app and decrypt books on this device.",
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
                await FinishUnlockedAsync(secret: null);
            else
                ErrorText.Text = "Windows Hello cancelled or unavailable. Try again, or clear lock in Settings after OS login.";
        }
    }

    private async void Unlock_Click(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        ErrorText.Text = "";
        if (!AppLockService.VerifyPinOrPassword(SecretBox.Password))
        {
            ErrorText.Text = "Incorrect PIN or password.";
            return;
        }
        await FinishUnlockedAsync(SecretBox.Password);
    }

    private async void Hello_Click(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        ErrorText.Text = "";
        var ok = await AppLockService.TryWindowsHelloAsync();
        if (ok)
            await FinishUnlockedAsync(secret: null);
        else
            ErrorText.Text = "Windows Hello did not verify.";
    }

    private async Task FinishUnlockedAsync(string? secret)
    {
        if (_busy) return;
        _busy = true;
        UnlockBtn.IsEnabled = false;
        HelloBtn.IsEnabled = false;
        try
        {
            // Unseal ledger (no-op if encryption not enabled)
            var dbOk = await AppLockService.UnlockDatabaseAsync(secret);
            if (!dbOk)
            {
                // Engine may still be starting or encryption off
                try
                {
                    using var api = new LedgerApiClient();
                    await api.EnsureBackendAsync();
                    var health = await api.HealthAsync();
                    if (!health)
                    {
                        ErrorText.Text = "Could not reach the money engine. Try again.";
                        return;
                    }
                }
                catch (Exception ex)
                {
                    ErrorText.Text = "Unlock UI ok but database unlock failed: " + ex.Message;
                    return;
                }
            }
            if (App.MainWindowInstance is MainWindow mw)
                mw.OnAppUnlocked();
        }
        finally
        {
            _busy = false;
            UnlockBtn.IsEnabled = true;
            HelloBtn.IsEnabled = true;
        }
    }
}
