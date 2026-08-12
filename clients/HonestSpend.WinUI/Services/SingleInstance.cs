using System.Diagnostics;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// Named mutex + event so a second launch activates the first window.
/// Handles abandoned mutexes (previous crash) so the user is not stuck with "instant close".
/// </summary>
public static class SingleInstance
{
    private const string MutexName = "Local\\HonestSpend.WinUI.SingleInstance";
    private const string EventName = "Local\\HonestSpend.WinUI.Show";

    private static Mutex? _mutex;
    private static EventWaitHandle? _showEvent;
    private static CancellationTokenSource? _cts;

    private static void Log(string msg)
    {
        try
        {
            var dir = WinUiPaths.DefaultLocalDataDir();
            Directory.CreateDirectory(dir);
            File.AppendAllText(
                Path.Combine(dir, "winui-lifecycle.log"),
                $"[{DateTime.Now:O}] [pid={Environment.ProcessId}] {msg}\n");
        }
        catch
        {
            /* ignore */
        }
    }

    /// <summary>Returns false if another instance owns the mutex (this process should exit).</summary>
    public static bool TryAcquire()
    {
        try
        {
            try
            {
                AppConfig.ApplyCommandLine(Environment.GetCommandLineArgs());
            }
            catch { /* ignore */ }

            // initiallyOwned: false — then WaitOne so AbandonedMutexException is handled
            _mutex = new Mutex(initiallyOwned: false, MutexName, out _);
            try
            {
                if (!_mutex.WaitOne(0))
                {
                    Log("Second instance — signaling existing window, exiting this process");
                    if (!string.IsNullOrWhiteSpace(AppConfig.OpenPage))
                        WinUiPaths.WriteNavigateRequest(AppConfig.OpenPage!);
                    try
                    {
                        using var ev = EventWaitHandle.OpenExisting(EventName);
                        ev.Set();
                    }
                    catch (Exception ex)
                    {
                        Log("Could not signal show event: " + ex.Message);
                    }
                    try { _mutex.Dispose(); } catch { /* ignore */ }
                    _mutex = null;
                    return false;
                }
            }
            catch (AbandonedMutexException)
            {
                // Previous process crashed without releasing — we own it now.
                Log("Acquired abandoned mutex (previous crash) — continuing as primary");
            }

            _showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, EventName);
            Log("Primary instance acquired");
            return true;
        }
        catch (Exception ex)
        {
            Log("Mutex failed, allowing run: " + ex.Message);
            return true;
        }
    }

    public static void StartShowListener(Action onShow)
    {
        if (_showEvent is null) return;
        _cts = new CancellationTokenSource();
        var token = _cts.Token;
        _ = Task.Run(() =>
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    if (_showEvent.WaitOne(500))
                    {
                        Log("Show event received");
                        try { onShow(); } catch { /* ignore */ }
                    }
                }
                catch
                {
                    break;
                }
            }
        }, token);
    }

    public static void Release()
    {
        try { _cts?.Cancel(); } catch { /* ignore */ }
        try { _showEvent?.Dispose(); } catch { /* ignore */ }
        try
        {
            _mutex?.ReleaseMutex();
            _mutex?.Dispose();
        }
        catch
        {
            /* ignore */
        }
        _mutex = null;
        _showEvent = null;
        Log("Released single-instance lock");
    }
}
