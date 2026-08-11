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
                "Enter your PIN. This unlocks the app and decrypts your books.",
            AppLockService.LockMode.Password =>
                "Enter your password. This unlocks the app and decrypts your books.",
            AppLockService.LockMode.Platform =>
                "Use Windows Hello to unlock the app and decrypt books on this device.",
            _ => "Unlock your encrypted books to continue.",
        };
        SecretBox.Visibility = mode is AppLockService.LockMode.Pin or AppLockService.LockMode.Password or AppLockService.LockMode.None
            ? Visibility.Visible
            : Visibility.Collapsed;
        // If only encryption (edge) show PIN box
        if (mode == AppLockService.LockMode.None)
        {
            HintText.Text =
                "Books need to be unlocked. Enter your PIN/password if you set one, " +
                "or tap Continue if encryption is off.";
            UnlockBtn.Content = "Unlock / Continue";
        }
        UnlockBtn.Visibility = mode is not AppLockService.LockMode.Platform
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
        var secret = SecretBox.Password ?? "";
        if (AppLockService.Mode is AppLockService.LockMode.Pin or AppLockService.LockMode.Password)
        {
            if (!AppLockService.VerifyPinOrPassword(secret))
            {
                ErrorText.Text = "Incorrect PIN or password.";
                return;
            }
        }
        else if (string.IsNullOrEmpty(secret))
        {
            // No UI lock + empty secret: allow only if books already ready (encryption off)
            AppLockService.MarkUnlocked();
            using var api = new LedgerApiClient();
            try
            {
                await api.EnsureBackendAsync();
                if (await api.BooksReadyAsync())
                {
                    if (App.MainWindowInstance is MainWindow mw)
                        mw.OnAppUnlocked();
                    return;
                }
            }
            catch { /* fall through */ }
            ErrorText.Text = "Enter your PIN or password to decrypt books.";
            return;
        }
        else
        {
            AppLockService.MarkUnlocked();
        }
        await FinishUnlockedAsync(string.IsNullOrEmpty(secret) ? null : secret);
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
        ScenarioMsgSafe("Decrypting books…");
        try
        {
            var dbOk = await AppLockService.UnlockDatabaseAsync(secret);
            if (!dbOk)
            {
                ErrorText.Text =
                    "Could not decrypt books. Check your PIN/password, or wait for the engine and try again. " +
                    "The app will not open until books are unlocked.";
                AppLockService.LockSession();
                return;
            }
            // Fail closed: require books_ready
            using var api = new LedgerApiClient();
            if (!await api.BooksReadyAsync())
            {
                ErrorText.Text = "Engine is up but books are still locked. Try unlock again.";
                AppLockService.LockSession();
                return;
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

    private void ScenarioMsgSafe(string msg)
    {
        try { ErrorText.Text = ""; HintText.Text = msg; } catch { /* ignore */ }
    }
}
