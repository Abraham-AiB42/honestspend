using System.Diagnostics;
using System.Runtime.InteropServices;

namespace LedgerRing_WinUI.Services;

/// <summary>
/// Named mutex + event so a second launch activates the first window.
/// </summary>
public static class SingleInstance
{
    private const string MutexName = "Local\\LedgerRing.WinUI.SingleInstance";
    private const string EventName = "Local\\LedgerRing.WinUI.Show";

    private static Mutex? _mutex;
    private static EventWaitHandle? _showEvent;
    private static CancellationTokenSource? _cts;

    /// <summary>Returns false if another instance owns the mutex (this process should exit).</summary>
    public static bool TryAcquire()
    {
        try
        {
            _mutex = new Mutex(initiallyOwned: true, MutexName, out var createdNew);
            if (!createdNew)
            {
                try
                {
                    using var ev = EventWaitHandle.OpenExisting(EventName);
                    ev.Set();
                }
                catch
                {
                    /* first instance may not have registered yet */
                }
                try { _mutex.Dispose(); } catch { /* ignore */ }
                _mutex = null;
                return false;
            }

            _showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, EventName);
            return true;
        }
        catch
        {
            // If mutex fails, allow run (better than blocking the user)
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
    }
}
