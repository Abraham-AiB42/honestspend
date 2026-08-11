using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace HonestSpend_WinUI.Services;

public sealed class LedgerApiClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly HttpClient _http;

    public LedgerApiClient(string? baseUrl = null, string? apiKey = null)
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri((baseUrl ?? AppConfig.BaseUrl).TrimEnd('/') + "/"),
            Timeout = TimeSpan.FromSeconds(60),
        };
        var key = apiKey ?? AppConfig.ApiKey;
        if (!string.IsNullOrWhiteSpace(key))
            _http.DefaultRequestHeaders.TryAddWithoutValidation("X-API-Key", key);
    }

    public async Task EnsureBackendAsync(CancellationToken ct = default)
    {
        if (App.Backend is not null)
            await App.Backend.EnsureRunningAsync(ct);
    }

    public async Task<bool> HealthAsync(CancellationToken ct = default)
    {
        try
        {
            var r = await _http.GetAsync("api/health", ct);
            return r.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    public Task<JsonElement> GetIfppAsync(
        string mode = "conservative",
        int? profileId = null,
        string? scope = null,
        CancellationToken ct = default)
    {
        var sc = scope ?? AppState.IfppScope;
        var q = $"api/ifpp?mode={Uri.EscapeDataString(mode)}&scope={Uri.EscapeDataString(sc)}";
        var pid = profileId ?? AppState.SelectedProfileId;
        if (pid is not null && sc == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetCapitalDeskAsync(
        int? profileId = null,
        string? scope = null,
        CancellationToken ct = default)
    {
        var sc = scope ?? AppState.IfppScope;
        var q = $"api/capital-desk?scope={Uri.EscapeDataString(sc)}";
        var pid = profileId ?? AppState.SelectedProfileId;
        if (pid is not null && sc == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetDigestAsync(
        int? profileId = null,
        string? scope = null,
        CancellationToken ct = default)
    {
        var sc = scope ?? AppState.IfppScope;
        var q = $"api/digest?scope={Uri.EscapeDataString(sc)}";
        var pid = profileId ?? AppState.SelectedProfileId;
        if (pid is not null && sc == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetDigestBriefAsync(bool useGrok = true, CancellationToken ct = default)
    {
        var sc = AppState.IfppScope;
        var q = $"api/digest/brief?scope={Uri.EscapeDataString(sc)}&use_grok={useGrok.ToString().ToLowerInvariant()}";
        if (AppState.SelectedProfileId is int pid && sc == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetAutopayAsync(CancellationToken ct = default)
    {
        var q = "api/autopay";
        if (AppState.SelectedProfileId is int pid && AppState.IfppScope == "entity")
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> SetAutopayAsync(int accountId, string policy, bool applySchedule = true, CancellationToken ct = default)
        => PutJsonAsync($"api/autopay/{accountId}", new { policy, apply_schedule = applySchedule }, ct);

    public Task<JsonElement> GetIntermixGraphAsync(int days = 365, CancellationToken ct = default)
        => GetJsonAsync($"api/intermix/graph?days={days}", ct);

    public Task<JsonElement> GetGlanceAsync(CancellationToken ct = default)
    {
        var sc = AppState.IfppScope;
        var q = $"api/glance?scope={Uri.EscapeDataString(sc)}";
        if (AppState.SelectedProfileId is int pid && sc == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetHomeSimpleAsync(CancellationToken ct = default)
    {
        var sc = AppState.IfppScope;
        var q = $"api/home/simple?scope={Uri.EscapeDataString(sc)}";
        if (AppState.SelectedProfileId is int pid && sc == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> FirstRunAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/onboarding/first-run", body, ct);

    public Task<JsonElement> GetPaymentCandidatesAsync(int days = 14, CancellationToken ct = default)
    {
        var q = $"api/payments/candidates?days={days}";
        if (AppState.SelectedProfileId is int pid && AppState.IfppScope == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> ConfirmPaymentAsync(int cashTxnId, int cardTxnId, CancellationToken ct = default)
        => PostJsonAsync("api/payments/confirm", new { cash_txn_id = cashTxnId, card_txn_id = cardTxnId }, ct);


    public Task<JsonElement> GetPromoClockAsync(CancellationToken ct = default)
        => GetJsonAsync("api/promo-clock", ct);

    public Task<JsonElement> CreatePromoSinkBillAsync(int accountId, CancellationToken ct = default)
        => PostJsonAsync($"api/promo-clock/{accountId}/sink-bill", new { }, ct);

    public Task<JsonElement> GetFeeCandidatesAsync(int days = 90, int limit = 50, CancellationToken ct = default)
    {
        var q = $"api/fees/candidates?days={days}&limit={limit}";
        if (AppState.SelectedProfileId is int pid && AppState.IfppScope == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> ConfirmFeeAsync(int transactionId, string action, int? categoryId = null, CancellationToken ct = default)
        => PostJsonAsync("api/fees/confirm", new
        {
            transaction_id = transactionId,
            action,
            category_id = categoryId,
        }, ct);

    public Task<JsonElement> AcceptRecurringAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/recurring/accept", body, ct);

    public Task<JsonElement> MarkMonthClosedAsync(bool force = false, CancellationToken ct = default)
        => PostJsonAsync("api/home/month-close/complete", new { force }, ct);

    public Task<JsonElement> GetCashflowReportAsync(int days = 30, CancellationToken ct = default)
        => GetJsonAsync($"api/reports/cashflow?days={days}", ct);

    public Task<JsonElement> GetDebtReportAsync(CancellationToken ct = default)
    {
        var q = "api/reports/debt";
        if (AppState.SelectedProfileId is int pid && AppState.IfppScope == "entity")
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> QuickScenarioAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/scenarios/quick", body, ct);

    public Task<JsonElement> ListScenariosAsync(CancellationToken ct = default)
    {
        var q = "api/scenarios";
        if (AppState.SelectedProfileId is int pid && AppState.IfppScope == "entity")
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> RunScenarioAsync(int scenarioId, CancellationToken ct = default)
        => PostJsonAsync($"api/scenarios/{scenarioId}/run", new { }, ct);

    public async Task DeleteScenarioAsync(int scenarioId, CancellationToken ct = default)
    {
        var r = await _http.DeleteAsync($"api/scenarios/{scenarioId}", ct);
        if (!r.IsSuccessStatusCode)
        {
            var body = await r.Content.ReadAsStringAsync(ct);
            throw new HttpRequestException($"{(int)r.StatusCode} api/scenarios/{scenarioId}: {body}");
        }
    }

    public Task<JsonElement> GetFeeSummaryAsync(int days = 365, CancellationToken ct = default)
    {
        var q = $"api/fees/summary?days={days}";
        if (AppState.SelectedProfileId is int pid && AppState.IfppScope == "entity")
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> LiquidityRescueAsync(decimal? amount = null, decimal? shortfall = null, CancellationToken ct = default)
        => PostJsonAsync("api/liquidity/rescue", new
        {
            amount,
            shortfall,
            profile_id = AppState.SelectedProfileId,
            scope = AppState.IfppScope,
        }, ct);

    public Task<JsonElement> GetReconcileAsync(int? profileId = null, CancellationToken ct = default)
    {
        var q = "api/reconcile";
        if (profileId is not null) q += $"?profile_id={profileId}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> SetInstitutionBalanceAsync(
        int accountId, decimal balance, bool markReconciled = false, CancellationToken ct = default)
        => PostJsonAsync($"api/reconcile/{accountId}/institution-balance", new
        {
            balance,
            mark_reconciled = markReconciled,
        }, ct);

    public Task<JsonElement> ReconcileTrustAsync(int accountId, string trust, CancellationToken ct = default)
        => PostJsonAsync($"api/reconcile/{accountId}/trust", new { trust }, ct);

    public Task<JsonElement> PlaidDisconnectAsync(int itemPk, bool keepAccounts = true, CancellationToken ct = default)
        => PostJsonAsync(
            $"api/plaid/disconnect/{itemPk}?keep_accounts={keepAccounts.ToString().ToLowerInvariant()}",
            new { }, ct);

    public async Task<JsonElement> PreviewBankCsvAsync(Stream fileStream, string fileName, CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("text/csv");
        content.Add(streamContent, "file", fileName);
        const string q = "api/import/bank-csv/preview";
        var r = await _http.PostAsync(q, content, ct);
        return await ReadJsonAsync(r, q, ct);
    }

    public Task<JsonElement> PrePurchaseAsync(
        decimal amount,
        string prefer = "auto",
        int? profileId = null,
        string? scope = null,
        int? categoryId = null,
        CancellationToken ct = default)
        => PostJsonAsync("api/pre-purchase", new
        {
            amount,
            prefer,
            profile_id = profileId ?? AppState.SelectedProfileId,
            scope = scope ?? AppState.IfppScope,
            category_id = categoryId is > 0 ? categoryId : null,
        }, ct);

    public Task<JsonElement> GetTaxVaultAsync(CancellationToken ct = default)
        => GetJsonAsync("api/tax-vault", ct);

    public Task<JsonElement> PutTaxVaultAsync(object body, CancellationToken ct = default)
        => PutJsonAsync("api/tax-vault", body, ct);

    public Task<JsonElement> AdjustTaxVaultAsync(decimal delta, string? note = null, CancellationToken ct = default)
        => PostJsonAsync("api/tax-vault/adjust", new { delta, note }, ct);

    public Task<JsonElement> GetProfilesAsync(CancellationToken ct = default)
        => GetJsonAsync("api/profiles", ct);

    public Task<JsonElement> CreateProfileAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/profiles", body, ct);

    public Task<JsonElement> ArchiveProfileAsync(int id, CancellationToken ct = default)
        => PostJsonAsync($"api/profiles/{id}/archive", new { }, ct);

    public Task<JsonElement> VoidTransactionAsync(int id, string? reason = null, CancellationToken ct = default)
        => PostJsonAsync($"api/transactions/{id}/void", new { reason }, ct);

    public Task<JsonElement> GetTransferCandidatesAsync(int days = 7, CancellationToken ct = default)
    {
        var q = $"api/transfers/candidates?days={days}";
        if (AppState.SelectedProfileId is int pid)
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> ConfirmTransferAsync(int outTxnId, int inTxnId, CancellationToken ct = default)
        => PostJsonAsync("api/transfers/confirm", new { out_txn_id = outTxnId, in_txn_id = inTxnId }, ct);

    public Task<JsonElement> SimulateIfppAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/ifpp/simulate", body, ct);

    public Task<JsonElement> GetAccountsAsync(int? profileId = null, CancellationToken ct = default)
        => GetJsonAsync(profileId is null ? "api/accounts" : $"api/accounts?profile_id={profileId}", ct);

    public Task<JsonElement> CreateAccountAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/accounts", body, ct);

    public Task<JsonElement> PatchAccountAsync(int id, object body, CancellationToken ct = default)
        => PatchJsonAsync($"api/accounts/{id}", body, ct);

    public Task<JsonElement> ArchiveAccountAsync(int id, CancellationToken ct = default)
        => PostJsonAsync($"api/accounts/{id}/archive", new { }, ct);

    public Task<JsonElement> UnarchiveAccountAsync(int id, CancellationToken ct = default)
        => PostJsonAsync($"api/accounts/{id}/unarchive", new { }, ct);

    public Task<JsonElement> PutAccountAsync(int id, object body, CancellationToken ct = default)
        => PutJsonAsync($"api/accounts/{id}", body, ct);

    public Task<JsonElement> GetScheduledAsync(bool activeOnly = true, CancellationToken ct = default)
        => GetJsonAsync($"api/scheduled?active_only={activeOnly.ToString().ToLowerInvariant()}", ct);

    public Task<JsonElement> CreateScheduledAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/scheduled", body, ct);

    public Task<JsonElement> EndScheduledAsync(int id, string? reason = null, CancellationToken ct = default)
        => PostJsonAsync($"api/scheduled/{id}/end", new { reason }, ct);

    public Task<JsonElement> GetDebtPlanAsync(string strategy = "avalanche", decimal extra = 0, CancellationToken ct = default)
        => PostJsonAsync("api/debt/plan", new
        {
            strategy,
            extra_monthly = extra,
            save_preference = false,
            opportunity_cost_aware = true,
        }, ct);

    public Task<JsonElement> GetDebtCompareAsync(decimal extra = 0, CancellationToken ct = default)
        => GetJsonAsync($"api/debt/compare?extra_monthly={extra}", ct);

    public Task<JsonElement> GetCreditHealthAsync(CancellationToken ct = default)
        => GetJsonAsync("api/credit/health", ct);

    public Task<JsonElement> IntermixAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/intermix", body, ct);

    public Task<JsonElement> GetTransactionsAsync(int limit = 100, bool uncategorized = false, int? profileId = null, CancellationToken ct = default)
    {
        var q = $"api/transactions?limit={limit}&uncategorized={uncategorized.ToString().ToLowerInvariant()}";
        if (profileId is not null) q += $"&profile_id={profileId}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> CreateTransactionAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/transactions", body, ct);

    public Task<JsonElement> PatchTransactionAsync(int id, object body, bool learn = true, CancellationToken ct = default)
        => PatchJsonAsync($"api/transactions/{id}?learn={learn.ToString().ToLowerInvariant()}", body, ct);

    public Task<JsonElement> GetCategoriesAsync(int? profileId = null, CancellationToken ct = default)
        => GetJsonAsync(profileId is null ? "api/categories" : $"api/categories?profile_id={profileId}", ct);

    public Task<JsonElement> CategorizeBatchAsync(bool apply = false, bool useGrok = false, int limit = 80, CancellationToken ct = default)
        => PostJsonAsync("api/categorize/batch", new
        {
            apply,
            use_grok = useGrok,
            limit,
            min_confidence = apply ? 0.85 : (double?)null,
        }, ct);

    public Task<JsonElement> GetSettingsAsync(CancellationToken ct = default)
        => GetJsonAsync("api/settings", ct);

    public Task<JsonElement> PutSettingsAsync(object body, CancellationToken ct = default)
        => PutJsonAsync("api/settings", body, ct);

    public Task<JsonElement> PatchSettingsAsync(object body, CancellationToken ct = default)
        => PatchJsonAsync("api/settings", body, ct);

    public Task<JsonElement> GetImportReminderAsync(CancellationToken ct = default)
        => GetJsonAsync("api/import/reminder", ct);

    public Task<JsonElement> SnoozeImportReminderAsync(int days = 7, CancellationToken ct = default)
        => PostJsonAsync("api/import/reminder/snooze", new { days }, ct);

    public Task<JsonElement> AckImportReminderAsync(CancellationToken ct = default)
        => PostJsonAsync("api/import/reminder/ack", new { }, ct);

    public Task<JsonElement> GetBankGuidesAsync(CancellationToken ct = default)
        => GetJsonAsync("api/import/bank-guides", ct);

    public Task<JsonElement> GetImportInboxAsync(CancellationToken ct = default)
        => GetJsonAsync("api/import/inbox", ct);

    public Task<JsonElement> ProcessImportInboxAsync(
        int? defaultAccountId = null,
        bool autoCategorize = true,
        string amountSign = "bank",
        bool dryRun = false,
        CancellationToken ct = default)
        => PostJsonAsync("api/import/inbox/process", new
        {
            default_account_id = defaultAccountId,
            auto_categorize = autoCategorize,
            amount_sign = amountSign,
            dry_run = dryRun,
        }, ct);

    public Task<JsonElement> GetPlaidStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("api/plaid/status", ct);

    public Task<JsonElement> GetPlaidCredentialsAsync(CancellationToken ct = default)
        => GetJsonAsync("api/plaid/credentials", ct);

    public Task<JsonElement> SavePlaidCredentialsAsync(
        string clientId, string secret, string env = "sandbox", CancellationToken ct = default)
        => PostJsonAsync("api/plaid/credentials", new
        {
            client_id = clientId,
            secret,
            env,
        }, ct);

    public Task<JsonElement> GetAiCredentialsAsync(CancellationToken ct = default)
        => GetJsonAsync("api/ai/credentials", ct);

    public Task<JsonElement> SaveAiCredentialsAsync(
        string provider, string apiKey, string? baseUrl = null, CancellationToken ct = default)
        => PostJsonAsync("api/ai/credentials", new
        {
            provider,
            api_key = apiKey,
            base_url = baseUrl,
        }, ct);

    public Task<JsonElement> GetPlaidItemsAsync(CancellationToken ct = default)
        => GetJsonAsync("api/plaid/items", ct);

    public Task<JsonElement> CreatePlaidLinkTokenAsync(CancellationToken ct = default)
        => PostJsonAsync("api/plaid/link-token", new { }, ct);

    public Task<JsonElement> PlaidExchangeAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/plaid/exchange", body, ct);

    public Task<JsonElement> PlaidSyncAsync(int itemPk, CancellationToken ct = default)
        => PostJsonAsync($"api/plaid/sync/{itemPk}", new { }, ct);

    public Task<JsonElement> PlaidSandboxLinkAsync(int profileId, CancellationToken ct = default)
        => PostJsonAsync($"api/plaid/sandbox-link?profile_id={profileId}", new { }, ct);

    public Task<JsonElement> GetOnboardingAsync(CancellationToken ct = default)
        => GetJsonAsync("api/onboarding", ct);

    public Task<JsonElement> GetSetupStateAsync(CancellationToken ct = default)
        => GetJsonAsync("api/setup/state", ct);

    public Task<JsonElement> SetupAdvanceAsync(
        string action = "next",
        string? path = null,
        string? targetPhase = null,
        object? payload = null,
        CancellationToken ct = default)
        => PostJsonAsync("api/setup/advance", new
        {
            action,
            path,
            target_phase = targetPhase,
            payload,
        }, ct);

    public Task<JsonElement> SetupCompleteAsync(string? note = null, CancellationToken ct = default)
        => PostJsonAsync("api/setup/complete", new { note }, ct);

    public Task<JsonElement> GetSetupStorageAsync(CancellationToken ct = default)
        => GetJsonAsync("api/setup/storage", ct);

    public Task<JsonElement> PostSetupStorageAsync(
        string kind,
        string? path = null,
        bool advance = true,
        CancellationToken ct = default)
        => PostJsonAsync("api/setup/storage", new { kind, path, advance }, ct);

    public Task<JsonElement> GetSetupSecurityAsync(string platform = "win", CancellationToken ct = default)
        => GetJsonAsync($"api/setup/security?platform={Uri.EscapeDataString(platform)}", ct);

    public Task<JsonElement> PostSetupSecurityAsync(
        string mode,
        string? platformCapability = null,
        bool advance = true,
        CancellationToken ct = default)
        => PostJsonAsync("api/setup/security", new
        {
            mode,
            platform_capability = platformCapability,
            advance,
        }, ct);

    public Task<JsonElement> CryptoStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("api/crypto/status", ct);

    public Task<JsonElement> CryptoUnlockAsync(
        string? secret = null,
        string? dekB64 = null,
        CancellationToken ct = default)
        => PostJsonAsync("api/crypto/unlock", new { secret, dek_b64 = dekB64 }, ct);

    public Task<JsonElement> CryptoEnableAsync(
        string? secret = null,
        string modeHint = "pin",
        string wrap = "password",
        string? dekB64 = null,
        CancellationToken ct = default)
        => PostJsonAsync("api/crypto/enable", new
        {
            secret,
            mode_hint = modeHint,
            wrap,
            dek_b64 = dekB64,
        }, ct);

    public Task<JsonElement> CryptoLockAsync(CancellationToken ct = default)
        => PostJsonAsync("api/crypto/lock", new { }, ct);

    public Task<JsonElement> CryptoDisableAsync(
        string? secret = null,
        string? dekB64 = null,
        CancellationToken ct = default)
        => PostJsonAsync("api/crypto/disable", new { secret, dek_b64 = dekB64 }, ct);

    public Task<JsonElement> GetSetupCashAsync(int? profileId = null, CancellationToken ct = default)
    {
        var q = "api/setup/cash";
        if (profileId is int pid)
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> CreateSetupCashAccountAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/setup/cash-account", body, ct);

    public Task<JsonElement> GetSetupDiscoverAsync(
        int? profileId = null, int lookbackDays = 90, CancellationToken ct = default)
    {
        var q = $"api/setup/discover?lookback_days={lookbackDays}";
        if (profileId is int pid)
            q += $"&profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> ApplySetupDiscoverAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/setup/discover/apply", body, ct);

    public Task<JsonElement> SetAccountPaymentOptionAsync(
        int accountId, string paymentOption, decimal? fixedAmount = null, CancellationToken ct = default)
        => PostJsonAsync($"api/accounts/{accountId}/payment-option", new
        {
            payment_option = paymentOption,
            payment_fixed_amount = fixedAmount,
        }, ct);

    public Task<JsonElement> GetSetupRecurringAsync(CancellationToken ct = default)
        => GetJsonAsync("api/setup/recurring", ct);

    public Task<JsonElement> ApplySetupRecurringAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/setup/recurring/apply", body, ct);

    public Task<JsonElement> GetSetupCategorizeAsync(int confirmCap = 20, CancellationToken ct = default)
        => GetJsonAsync($"api/setup/categorize?confirm_cap={confirmCap}", ct);

    public Task<JsonElement> SetupCategorizeAutoAsync(bool useGrok = false, CancellationToken ct = default)
        => PostJsonAsync("api/setup/categorize/auto", new { use_grok = useGrok, min_confidence = 0.85 }, ct);

    public Task<JsonElement> SetupCategorizeConfirmAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/setup/categorize/confirm", body, ct);

    public Task<JsonElement> GetSetupBudgetsAsync(bool seedIfEmpty = false, CancellationToken ct = default)
        => GetJsonAsync($"api/setup/budgets?seed_if_empty={seedIfEmpty.ToString().ToLowerInvariant()}", ct);

    public Task<JsonElement> SeedSetupBudgetsAsync(CancellationToken ct = default)
        => PostJsonAsync("api/setup/budgets/seed", new { }, ct);

    public Task<JsonElement> ApplySetupBudgetsAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/setup/budgets/apply", body, ct);

    public Task<JsonElement> GetSetupBuffersAsync(CancellationToken ct = default)
        => GetJsonAsync("api/setup/buffers", ct);

    public Task<JsonElement> SaveSetupBuffersAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/setup/buffers", body, ct);

    public Task<JsonElement> CompleteOnboardingAsync(CancellationToken ct = default)
        => PostJsonAsync("api/onboarding/complete", new { }, ct);

    public Task<JsonElement> QuickSetupAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/onboarding/quick-setup", body, ct);

    public Task<JsonElement> GetTaxCoaSummaryAsync(CancellationToken ct = default)
        => GetJsonAsync("api/tax/coa-summary", ct);

    public Task<JsonElement> PatchProfileAsync(int profileId, object body, CancellationToken ct = default)
        => PatchJsonAsync($"api/profiles/{profileId}", body, ct);

    public Task<JsonElement> GetTaxReadinessAsync(int profileId, int? year = null, CancellationToken ct = default)
    {
        var q = $"api/tax/readiness?profile_id={profileId}";
        if (year is not null) q += $"&year={year}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetTaxPacketAsync(int profileId, int? year = null, CancellationToken ct = default)
    {
        var q = $"api/tax/packet?profile_id={profileId}";
        if (year is not null) q += $"&year={year}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> WriteTaxPacketAsync(int profileId, int? year = null, CancellationToken ct = default)
    {
        var q = $"api/tax/packet/write?profile_id={profileId}";
        if (year is not null) q += $"&year={year}";
        return PostJsonAsync(q, new { }, ct);
    }

    public Task<JsonElement> CreateCpaPackMetaAsync(
        int profileId, int? year = null, bool issueToken = true, CancellationToken ct = default)
    {
        var q = $"api/tax/cpa-pack?profile_id={profileId}&issue_token={issueToken.ToString().ToLowerInvariant()}";
        if (year is not null) q += $"&year={year}";
        return PostJsonAsync(q, new { }, ct);
    }

    public async Task<byte[]> DownloadTaxPacketZipAsync(int profileId, int? year = null, CancellationToken ct = default)
    {
        var q = $"api/tax/packet/download?profile_id={profileId}";
        if (year is not null) q += $"&year={year}";
        var r = await _http.GetAsync(q, ct);
        var body = await r.Content.ReadAsByteArrayAsync(ct);
        if (!r.IsSuccessStatusCode)
        {
            var text = Encoding.UTF8.GetString(body);
            throw new HttpRequestException($"{(int)r.StatusCode} {q}: {text}");
        }
        return body;
    }

    public async Task<byte[]> DownloadCpaPackAsync(
        int profileId, int? year = null, bool issueToken = false, CancellationToken ct = default)
    {
        var q =
            $"api/tax/cpa-pack/download?profile_id={profileId}" +
            $"&issue_token={issueToken.ToString().ToLowerInvariant()}";
        if (year is not null) q += $"&year={year}";
        var r = await _http.GetAsync(q, ct);
        var body = await r.Content.ReadAsByteArrayAsync(ct);
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"{(int)r.StatusCode} {q}: {Encoding.UTF8.GetString(body)}");
        return body;
    }

    public Task<JsonElement> GetCreditStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("api/credit/status", ct);

    public Task<JsonElement> GetCreditProfileAsync(CancellationToken ct = default)
        => GetJsonAsync("api/credit/profile", ct);

    public Task<JsonElement> PutCreditProfileAsync(object body, CancellationToken ct = default)
        => PutJsonAsync("api/credit/profile", body, ct);

    public Task<JsonElement> GetBudgetStatusAsync(int? profileId = null, CancellationToken ct = default)
    {
        var q = "api/budgets/status";
        var pid = profileId ?? AppState.SelectedProfileId;
        if (pid is not null)
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetBudgetSuggestionsAsync(int? profileId = null, CancellationToken ct = default)
    {
        var q = "api/budgets/suggestions";
        var pid = profileId ?? AppState.SelectedProfileId;
        if (pid is not null)
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> GetBudgetCutsAsync(int? profileId = null, CancellationToken ct = default)
    {
        var q = "api/budgets/cuts";
        var pid = profileId ?? AppState.SelectedProfileId;
        if (pid is not null)
            q += $"?profile_id={pid}";
        return GetJsonAsync(q, ct);
    }

    public Task<JsonElement> CreateBudgetAsync(
        int profileId,
        int categoryId,
        string period,
        decimal amount,
        string? name = null,
        CancellationToken ct = default)
        => PostJsonAsync(
            "api/budgets",
            new
            {
                profile_id = profileId,
                category_id = categoryId,
                period,
                amount,
                name,
            },
            ct);

    public Task<JsonElement> ApplyBudgetCutAsync(
        int budgetRuleId,
        string kind,
        object? paramsObj = null,
        string? note = null,
        CancellationToken ct = default)
        => PostJsonAsync(
            "api/budgets/cuts/apply",
            new
            {
                budget_rule_id = budgetRuleId,
                kind,
                @params = paramsObj,
                note,
            },
            ct);

    public Task<JsonElement> AcceptBudgetSuggestionAsync(
        int profileId,
        int categoryId,
        string period,
        decimal? amount = null,
        string? name = null,
        CancellationToken ct = default)
        => PostJsonAsync(
            "api/budgets/suggestions/accept",
            new
            {
                profile_id = profileId,
                category_id = categoryId,
                period,
                amount,
                name,
            },
            ct);

    public Task<JsonElement> SeedBudgetsFromHistoryAsync(
        int? profileId = null,
        bool onlyIfEmpty = false,
        int maxRules = 10,
        CancellationToken ct = default)
        => PostJsonAsync(
            "api/budgets/seed-from-history",
            new
            {
                profile_id = profileId ?? AppState.SelectedProfileId,
                only_if_empty = onlyIfEmpty,
                max_rules = maxRules,
            },
            ct);

    public Task<JsonElement> GetLicenseAsync(CancellationToken ct = default)
        => GetJsonAsync("api/license", ct);

    public Task<JsonElement> ActivateLicenseAsync(string key, string? email = null, CancellationToken ct = default)
        => PostJsonAsync("api/license/activate", new { key, email }, ct);

    public Task<JsonElement> RegisterStoreLicenseAsync(
        bool isActive,
        bool isTrial = false,
        string? detail = null,
        string storeKind = "ms_store",
        string? storeSku = null,
        CancellationToken ct = default)
        => PostJsonAsync(
            "api/license/store",
            new
            {
                is_active = isActive,
                is_trial = isTrial,
                detail,
                store_kind = storeKind,
                store_sku = storeSku,
            },
            ct);

    public Task<JsonElement> ClearLicenseAsync(CancellationToken ct = default)
        => PostJsonAsync("api/license/clear", new { }, ct);

    public Task<JsonElement> RefreshLicenseAsync(CancellationToken ct = default)
        => PostJsonAsync("api/license/refresh", new { }, ct);

    public Task<JsonElement> GetSystemInfoAsync(CancellationToken ct = default)
        => GetJsonAsync("api/system/info", ct);

    public Task<JsonElement> GetSystemPathsAsync(CancellationToken ct = default)
        => GetJsonAsync("api/system/paths", ct);

    public Task<JsonElement> GetBackupStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("api/backup/status", ct);

    public Task<JsonElement> GetRemoteBackupConfigAsync(CancellationToken ct = default)
        => GetJsonAsync("api/backup/remote-config", ct);

    public Task<JsonElement> PutRemoteBackupConfigAsync(object body, CancellationToken ct = default)
        => PutJsonAsync("api/backup/remote-config", body, ct);

    public Task<JsonElement> CreateEncryptedBackupAsync(string password, string? note = null, bool copyToRemote = true, CancellationToken ct = default)
        => PostJsonAsync("api/backup/create-encrypted", new
        {
            password,
            note,
            copy_to_remote = copyToRemote,
        }, ct);

    public Task<JsonElement> CreateBackupAsync(bool asZip = true, string? note = null, CancellationToken ct = default)
        => PostJsonAsync("api/backup/create", new { as_zip = asZip, note }, ct);

    public Task<JsonElement> GetBackupScheduleAsync(CancellationToken ct = default)
        => GetJsonAsync("api/backup/schedule", ct);

    public Task<JsonElement> PutBackupScheduleAsync(object body, CancellationToken ct = default)
        => PutJsonAsync("api/backup/schedule", body, ct);

    public Task<JsonElement> RestoreBackupAsync(string name, CancellationToken ct = default)
        => PostJsonAsync($"api/backup/restore/{Uri.EscapeDataString(name)}", new { }, ct);

    public async Task<byte[]> DownloadBackupAsync(string name, CancellationToken ct = default)
    {
        var q = $"api/backup/download/{Uri.EscapeDataString(name)}";
        var r = await _http.GetAsync(q, ct);
        var body = await r.Content.ReadAsByteArrayAsync(ct);
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"{(int)r.StatusCode} {q}: {Encoding.UTF8.GetString(body)}");
        return body;
    }

    public async Task<byte[]> DownloadLiveBackupAsync(CancellationToken ct = default)
    {
        const string q = "api/backup/download-live";
        var r = await _http.GetAsync(q, ct);
        var body = await r.Content.ReadAsByteArrayAsync(ct);
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"{(int)r.StatusCode} {q}: {Encoding.UTF8.GetString(body)}");
        return body;
    }

    public async Task<JsonElement> RestoreBackupUploadAsync(Stream fileStream, string fileName, CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        content.Add(streamContent, "file", fileName);
        const string q = "api/backup/restore-upload";
        var r = await _http.PostAsync(q, content, ct);
        return await ReadJsonAsync(r, q, ct);
    }

    public Task<JsonElement> GetPermissionRolesAsync(CancellationToken ct = default)
        => GetJsonAsync("api/permissions/roles", ct);

    public Task<JsonElement> GetPermissionMeAsync(CancellationToken ct = default)
        => GetJsonAsync("api/permissions/me", ct);

    public Task<JsonElement> GetPermissionUsersAsync(CancellationToken ct = default)
        => GetJsonAsync("api/permissions/users", ct);

    public Task<JsonElement> CreatePermissionUserAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/permissions/users", body, ct);

    public Task<JsonElement> RotateTokenAsync(int userId, CancellationToken ct = default)
        => PostJsonAsync($"api/permissions/users/{userId}/rotate-token", new { }, ct);

    public Task<JsonElement> GetAuditAsync(int limit = 50, CancellationToken ct = default)
        => GetJsonAsync($"api/permissions/audit?limit={limit}", ct);

    public Task<JsonElement> GetRulesAsync(CancellationToken ct = default)
        => GetJsonAsync("api/rules", ct);

    public Task<JsonElement> TestRuleAsync(string matchType, string pattern, int limit = 80, CancellationToken ct = default)
        => PostJsonAsync("api/rules/test", new
        {
            match_type = matchType,
            pattern,
            limit,
        }, ct);

    public Task<JsonElement> CreateRuleAsync(object body, CancellationToken ct = default)
        => PostJsonAsync("api/rules", body, ct);

    public async Task DeleteRuleAsync(int id, CancellationToken ct = default)
    {
        var r = await _http.DeleteAsync($"api/rules/{id}", ct);
        if (!r.IsSuccessStatusCode)
        {
            var body = await r.Content.ReadAsStringAsync(ct);
            throw new HttpRequestException($"{(int)r.StatusCode} api/rules/{id}: {body}");
        }
    }

    public Task<JsonElement> GetCategorizerStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("api/categorizer/status", ct);

    public async Task<JsonElement> ImportBankCsvAsync(
        Stream fileStream,
        string fileName,
        int accountId,
        string amountSign = "bank",
        bool autoCategorize = true,
        decimal? institutionBalance = null,
        bool applyEndingBalance = true,
        CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("text/csv");
        content.Add(streamContent, "file", fileName);
        var q =
            $"api/import/bank-csv?account_id={accountId}" +
            $"&amount_sign={Uri.EscapeDataString(amountSign)}" +
            $"&auto_categorize={autoCategorize.ToString().ToLowerInvariant()}" +
            $"&apply_ending_balance={applyEndingBalance.ToString().ToLowerInvariant()}";
        if (institutionBalance is decimal bal)
            q += $"&institution_balance={Uri.EscapeDataString(bal.ToString(System.Globalization.CultureInfo.InvariantCulture))}";
        var r = await _http.PostAsync(q, content, ct);
        return await ReadJsonAsync(r, q, ct);
    }

    public async Task<JsonElement> PreviewOfxAsync(
        Stream fileStream,
        string fileName,
        CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/x-ofx");
        content.Add(streamContent, "file", fileName);
        var r = await _http.PostAsync("api/import/ofx/preview", content, ct);
        return await ReadJsonAsync(r, "api/import/ofx/preview", ct);
    }

    public async Task<JsonElement> ImportOfxAsync(
        Stream fileStream,
        string fileName,
        int accountId,
        string amountSign = "bank",
        bool autoCategorize = true,
        CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/x-ofx");
        content.Add(streamContent, "file", fileName);
        content.Add(new StringContent(accountId.ToString()), "account_id");
        content.Add(new StringContent(amountSign), "amount_sign");
        content.Add(new StringContent(autoCategorize.ToString().ToLowerInvariant()), "auto_categorize");
        var r = await _http.PostAsync("api/import/ofx", content, ct);
        return await ReadJsonAsync(r, "api/import/ofx", ct);
    }

    public async Task<JsonElement> PreviewStatementPdfAsync(
        Stream fileStream,
        string fileName,
        CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/pdf");
        content.Add(streamContent, "file", fileName);
        var r = await _http.PostAsync("api/import/statement-pdf/preview", content, ct);
        return await ReadJsonAsync(r, "api/import/statement-pdf/preview", ct);
    }

    public async Task<JsonElement> ImportStatementPdfAsync(
        Stream fileStream,
        string fileName,
        int accountId,
        string amountSign = "bank",
        bool autoCategorize = true,
        CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/pdf");
        content.Add(streamContent, "file", fileName);
        content.Add(new StringContent(accountId.ToString()), "account_id");
        content.Add(new StringContent(amountSign), "amount_sign");
        content.Add(new StringContent(autoCategorize.ToString().ToLowerInvariant()), "auto_categorize");
        var r = await _http.PostAsync("api/import/statement-pdf", content, ct);
        return await ReadJsonAsync(r, "api/import/statement-pdf", ct);
    }

    public async Task<JsonElement> ImportBudgetXlsxAsync(
        Stream fileStream,
        string fileName,
        string profileSlug = "personal",
        bool dryRun = false,
        CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        var streamContent = new StreamContent(fileStream);
        streamContent.Headers.ContentType =
            new System.Net.Http.Headers.MediaTypeHeaderValue(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
        content.Add(streamContent, "file", fileName);
        var q =
            $"api/import/budget-xlsx/upload?profile_slug={Uri.EscapeDataString(profileSlug)}" +
            $"&dry_run={dryRun.ToString().ToLowerInvariant()}";
        var r = await _http.PostAsync(q, content, ct);
        return await ReadJsonAsync(r, q, ct);
    }

    private async Task<JsonElement> GetJsonAsync(string path, CancellationToken ct)
    {
        var r = await _http.GetAsync(path, ct);
        return await ReadJsonAsync(r, path, ct);
    }

    private async Task<JsonElement> PostJsonAsync(string path, object payload, CancellationToken ct)
    {
        var r = await _http.PostAsJsonAsync(path, payload, JsonOpts, ct);
        return await ReadJsonAsync(r, path, ct);
    }

    private async Task<JsonElement> PutJsonAsync(string path, object payload, CancellationToken ct)
    {
        var r = await _http.PutAsJsonAsync(path, payload, JsonOpts, ct);
        return await ReadJsonAsync(r, path, ct);
    }

    private async Task<JsonElement> PatchJsonAsync(string path, object payload, CancellationToken ct)
    {
        var json = JsonSerializer.Serialize(payload, JsonOpts);
        using var content = new StringContent(json, Encoding.UTF8, "application/json");
        var r = await _http.PatchAsync(path, content, ct);
        return await ReadJsonAsync(r, path, ct);
    }

    private static async Task<JsonElement> ReadJsonAsync(HttpResponseMessage r, string path, CancellationToken ct)
    {
        var body = await r.Content.ReadAsStringAsync(ct);
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"{(int)r.StatusCode} {path}: {body}");
        return JsonSerializer.Deserialize<JsonElement>(body, JsonOpts);
    }

    public void Dispose() => _http.Dispose();
}
