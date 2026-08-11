using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Windows.Security.Credentials.UI;
using Windows.Security.Cryptography;
using Windows.Security.Cryptography.DataProtection;
using Windows.Storage;
using Windows.Storage.Streams;

namespace HonestSpend_WinUI.Services;

/// <summary>
/// Device-local app lock: none / PIN / password / Windows Hello.
/// Secrets never go to the engine or SQLite backups.
/// </summary>
public static class AppLockService
{
    private const string ResourceName = "HonestSpend.AppLock";
    private const int Pbkdf2Iterations = 200_000;
    private const int SaltSize = 16;
    private const int HashSize = 32;

    public enum LockMode
    {
        None,
        Pin,
        Password,
        Platform,
    }

    public static bool IsUnlocked { get; private set; }

    public static LockMode Mode
    {
        get
        {
            try
            {
                var ls = ApplicationData.Current.LocalSettings.Values;
                var m = (ls["AppLockMode"] as string ?? "none").ToLowerInvariant();
                return m switch
                {
                    "pin" => LockMode.Pin,
                    "password" => LockMode.Password,
                    "platform" => LockMode.Platform,
                    _ => LockMode.None,
                };
            }
            catch
            {
                return LockMode.None;
            }
        }
    }

    public static bool IsLockEnabled => Mode != LockMode.None;

    public static bool NeedsUnlock => IsLockEnabled && !IsUnlocked;

    public static void MarkUnlocked() => IsUnlocked = true;

    public static void LockSession() => IsUnlocked = false;

    public static string ModeId(LockMode mode) => mode switch
    {
        LockMode.Pin => "pin",
        LockMode.Password => "password",
        LockMode.Platform => "platform",
        _ => "none",
    };

    public static void ClearLock()
    {
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            ls.Remove("AppLockMode");
            ls.Remove("AppLockCapability");
            ls.Remove("AppLockSalt");
            ls.Remove("AppLockHash");
            ls.Remove("AppLockIter");
            ls.Remove("AppLockAlgo");
        }
        catch { /* ignore */ }
        IsUnlocked = true;
    }

    public static void SetNone()
    {
        ClearLock();
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            ls["AppLockMode"] = "none";
            ls.Remove("AppLockDek");
        }
        catch { /* ignore */ }
        IsUnlocked = true;
    }

    public static void SetPin(string pin)
    {
        pin = (pin ?? "").Trim();
        if (pin.Length < 4 || pin.Length > 8 || !pin.All(char.IsDigit))
            throw new InvalidOperationException("PIN must be 4–8 digits.");
        StoreSecret("pin", pin, capability: null);
        IsUnlocked = true;
    }

    public static void SetPassword(string password)
    {
        password ??= "";
        if (password.Length < 8)
            throw new InvalidOperationException("Password must be at least 8 characters.");
        StoreSecret("password", password, capability: null);
        IsUnlocked = true;
    }

    public static void SetPlatform(string capability = "windows_hello")
    {
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            ls["AppLockMode"] = "platform";
            ls["AppLockCapability"] = capability ?? "windows_hello";
            ls.Remove("AppLockSalt");
            ls.Remove("AppLockHash");
            ls.Remove("AppLockIter");
            ls.Remove("AppLockAlgo");
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException("Could not save Windows Hello lock: " + ex.Message, ex);
        }
        IsUnlocked = true;
    }

    /// <summary>Enable at-rest DB encryption with the same secret (PIN/password) or client DEK (Hello).</summary>
    public static async Task EnableDatabaseEncryptionAsync(
        string? secret,
        string modeHint,
        string wrap = "password",
        CancellationToken ct = default)
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync(ct);
        if (!await api.BooksReadyAsync(ct))
            throw new InvalidOperationException("Books must be open before enabling encryption.");
        // Engine must be open (plaintext) to enable
        var res = await api.CryptoEnableAsync(secret, modeHint, wrap, ct: ct);
        if (wrap == "client" && res.TryGetProperty("dek_b64", out var d) && d.ValueKind == JsonValueKind.String)
        {
            var dek = d.GetString();
            if (!string.IsNullOrEmpty(dek))
                await StoreProtectedDekAsync(dek);
        }
    }

    /// <summary>Unseal books in the engine after UI unlock. Returns true only when books are open.</summary>
    public static async Task<bool> UnlockDatabaseAsync(string? secret = null, CancellationToken ct = default)
    {
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync(ct);
            // Already open (encryption off or prior unlock)
            if (await api.BooksReadyAsync(ct))
                return true;

            string? dek = null;
            if (Mode == LockMode.Platform || secret is null)
                dek = await LoadProtectedDekAsync();

            // Retry while engine finishes booting into sealed mode
            Exception? last = null;
            for (var i = 0; i < 8; i++)
            {
                try
                {
                    await api.CryptoUnlockAsync(secret, dek, ct);
                    if (await api.BooksReadyAsync(ct))
                        return true;
                }
                catch (Exception ex)
                {
                    last = ex;
                    await Task.Delay(300, ct);
                }
            }
            if (last is not null)
                System.Diagnostics.Debug.WriteLine("UnlockDatabaseAsync: " + last.Message);
            return await api.BooksReadyAsync(ct);
        }
        catch
        {
            return false;
        }
    }

    public static async Task SealDatabaseAsync(CancellationToken ct = default)
    {
        try
        {
            using var api = new LedgerApiClient();
            // Call even if health is flaky — encryption may still be on
            try
            {
                await api.CryptoLockAsync(ct);
            }
            catch
            {
                // Engine may already be down
            }
        }
        catch { /* best-effort */ }
    }

    public static async Task DisableDatabaseEncryptionAsync(string? secret, CancellationToken ct = default)
    {
        using var api = new LedgerApiClient();
        await api.EnsureBackendAsync(ct);
        var dek = await LoadProtectedDekAsync();
        await api.CryptoDisableAsync(secret, dek, ct);
        try
        {
            ApplicationData.Current.LocalSettings.Values.Remove("AppLockDekProtected");
            ApplicationData.Current.LocalSettings.Values.Remove("AppLockDek"); // legacy cleartext
        }
        catch { /* ignore */ }
    }

    private static async Task StoreProtectedDekAsync(string dekB64)
    {
        try
        {
            var provider = new DataProtectionProvider("LOCAL=user");
            var clear = CryptographicBuffer.ConvertStringToBinary(dekB64, BinaryStringEncoding.Utf8);
            var protectedBuf = await provider.ProtectAsync(clear);
            CryptographicBuffer.CopyToByteArray(protectedBuf, out var bytes);
            ApplicationData.Current.LocalSettings.Values["AppLockDekProtected"] =
                Convert.ToBase64String(bytes);
            // Remove any legacy cleartext DEK
            ApplicationData.Current.LocalSettings.Values.Remove("AppLockDek");
        }
        catch
        {
            // Last resort (should be rare): still better than failing Hello setup silently
            ApplicationData.Current.LocalSettings.Values["AppLockDek"] = dekB64;
        }
    }

    private static async Task<string?> LoadProtectedDekAsync()
    {
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            if (ls["AppLockDekProtected"] is string b64 && !string.IsNullOrEmpty(b64))
            {
                var provider = new DataProtectionProvider("LOCAL=user");
                var raw = Convert.FromBase64String(b64);
                var buf = CryptographicBuffer.CreateFromByteArray(raw);
                var clear = await provider.UnprotectAsync(buf);
                return CryptographicBuffer.ConvertBinaryToString(BinaryStringEncoding.Utf8, clear);
            }
            // Legacy cleartext migration
            if (ls["AppLockDek"] is string legacy && !string.IsNullOrEmpty(legacy))
            {
                await StoreProtectedDekAsync(legacy);
                return legacy;
            }
        }
        catch { /* ignore */ }
        return null;
    }

    public static bool VerifyPinOrPassword(string secret)
    {
        secret ??= "";
        try
        {
            var ls = ApplicationData.Current.LocalSettings.Values;
            var mode = (ls["AppLockMode"] as string ?? "").ToLowerInvariant();
            if (mode is not ("pin" or "password"))
                return false;
            var saltB64 = ls["AppLockSalt"] as string;
            var hashB64 = ls["AppLockHash"] as string;
            var iter = ls["AppLockIter"] is int i ? i : Pbkdf2Iterations;
            if (string.IsNullOrEmpty(saltB64) || string.IsNullOrEmpty(hashB64))
                return false;
            var salt = Convert.FromBase64String(saltB64);
            var expected = Convert.FromBase64String(hashB64);
            var actual = Pbkdf2(secret, salt, iter);
            var ok = CryptographicOperations.FixedTimeEquals(actual, expected);
            if (ok) IsUnlocked = true;
            return ok;
        }
        catch
        {
            return false;
        }
    }

    public static async Task<bool> TryWindowsHelloAsync(string message = "Unlock HonestSpend")
    {
        try
        {
            var avail = await UserConsentVerifier.CheckAvailabilityAsync();
            if (avail != UserConsentVerifierAvailability.Available)
                return false;
            var result = await UserConsentVerifier.RequestVerificationAsync(message);
            if (result == UserConsentVerificationResult.Verified)
            {
                IsUnlocked = true;
                return true;
            }
            return false;
        }
        catch
        {
            return false;
        }
    }

    public static async Task<bool> IsWindowsHelloAvailableAsync()
    {
        try
        {
            var avail = await UserConsentVerifier.CheckAvailabilityAsync();
            return avail == UserConsentVerifierAvailability.Available;
        }
        catch
        {
            return false;
        }
    }

    public static async Task<bool> TryUnlockAsync(string? pinOrPassword = null)
    {
        if (!IsLockEnabled)
        {
            IsUnlocked = true;
            return true;
        }
        return Mode switch
        {
            LockMode.Platform => await TryWindowsHelloAsync(),
            LockMode.Pin or LockMode.Password => VerifyPinOrPassword(pinOrPassword ?? ""),
            _ => true,
        };
    }

    private static void StoreSecret(string mode, string secret, string? capability)
    {
        var salt = RandomNumberGenerator.GetBytes(SaltSize);
        var hash = Pbkdf2(secret, salt, Pbkdf2Iterations);
        var ls = ApplicationData.Current.LocalSettings.Values;
        ls["AppLockMode"] = mode;
        if (capability is not null)
            ls["AppLockCapability"] = capability;
        else
            ls.Remove("AppLockCapability");
        ls["AppLockSalt"] = Convert.ToBase64String(salt);
        ls["AppLockHash"] = Convert.ToBase64String(hash);
        ls["AppLockIter"] = Pbkdf2Iterations;
        ls["AppLockAlgo"] = "pbkdf2-sha256";
    }

    private static byte[] Pbkdf2(string secret, byte[] salt, int iterations)
    {
        return Rfc2898DeriveBytes.Pbkdf2(
            Encoding.UTF8.GetBytes(secret),
            salt,
            iterations,
            HashAlgorithmName.SHA256,
            HashSize);
    }

    /// <summary>Debug / support: export non-secret status.</summary>
    public static string StatusJson()
    {
        return JsonSerializer.Serialize(new
        {
            mode = ModeId(Mode),
            unlocked = IsUnlocked,
            enabled = IsLockEnabled,
        });
    }
}
