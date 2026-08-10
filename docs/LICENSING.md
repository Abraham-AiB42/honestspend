# HonestSpend licensing (buy once, all clients)

**Status:** design + local client stub (v1.0.32).  
**List price:** **$49.99 USD one-time** (lifetime personal / household).  
**Pre-launch promo (Partner Center):** **50% off** list until **2026-10-21** (ends end of day in store markets as configured).  
**Product model:** source stays open; **official store builds** are the paid commercial product.

---

## Goals

1. **One purchase** on any store (Microsoft · Apple · Google · optional direct) unlocks **all official clients**.
2. Ledger stays **local-first** — license service holds **entitlement only**, never bank data.
3. **OSS / self-build** remains usable without a store account (`FOS_LICENSE_ENFORCE=0` default).
4. Offline-friendly: **grace period** after last successful verify.

---

## What we sell

| Included | Not included |
|----------|----------------|
| Lifetime personal/household commercial license | Unlimited corporate site license (future SKU) |
| Official signed store builds + auto-update | Cloud ledger hosting |
| Activate on Windows / Mac / mobile clients | Bank password vault |
| Reasonable device count (default **5** activations) | Third-party Plaid/xAI fees (BYOK) |

Open source: audit and self-build under the project license (MIT today).  
Commercial redistribution of **signed binaries** and Store listing is controlled by Store terms + EULA.

---

## Cross-store entitlement (why a publisher license exists)

Stores do **not** share receipts. Flow:

```
Pay on Store A (or direct)
    → Store grants local entitlement on that platform
    → App registers purchase with HonestSpend license API
    → User gets license key and/or email-linked license
    → Store B / other PC: Activate with same key or email magic link
```

**Hybrid UX (target):**

1. Buy on any store.
2. App: **Activate all devices** → email or show **license key**.
3. Other clients: **Restore** (same store) **or** enter key / sign in email.

---

## Local state (engine)

File: `{data_dir}/license.json` (default `%USERPROFILE%\.financial-os\license.json`)

```json
{
  "schema": 1,
  "license_id": "lic_…",
  "key_fingerprint": "sha256:…",
  "email": "optional@example.com",
  "plan": "lifetime_personal",
  "max_devices": 5,
  "source": "key|ms_store|apple|google|direct|dev",
  "activated_at": "ISO-8601",
  "last_verified_at": "ISO-8601",
  "expires_at": null,
  "device_id": "stable machine id hash",
  "token": "optional opaque refresh token from server"
}
```

No full raw key stored after activate (fingerprint only), except optional masked display.

---

## Enforcement modes

| Env | Behavior |
|-----|----------|
| `FOS_LICENSE_ENFORCE=0` (default) | **OSS / freeware build** — always `licensed` for product use; Activate UI still works for testing |
| `FOS_LICENSE_ENFORCE=1` | Commercial / store build — requires valid local license within grace |
| `FOS_LICENSE_SERVER_URL` | Optional HTTPS base for verify/activate (Cloudflare Worker later) |
| `FOS_LICENSE_GRACE_DAYS` | Default **90** offline days after `last_verified_at` |

When enforce is on and license invalid: UI shows Activate (soft block first; hard gate later if desired).

---

## API (local engine)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/license` | Status for Home / Activate page |
| `POST` | `/api/license/activate` | Body: `{ "key", "email?" }` |
| `POST` | `/api/license/clear` | Remove local license |
| `POST` | `/api/license/refresh` | Re-verify with server if configured |
| `POST` | `/api/license/dev-activate` | Only when enforce off **or** `FOS_LICENSE_ALLOW_DEV_KEYS=1` |

**Dev / test keys** (never for production store builds):

- `HS-DEV-LIFETIME`
- `HS-TEST-LIFETIME`

---

## Remote license service (next backend)

Thin Cloudflare Worker + KV/D1:

| Endpoint | Role |
|----------|------|
| `POST /v1/activate` | key or store receipt → license record |
| `POST /v1/verify` | token/device → ok + grace refresh |
| `POST /v1/link-email` | magic link for cross-device |
| `POST /v1/store/ms` | Microsoft Store collection / JWT verify (when wired) |

**Privacy:** email optional; no ledger payloads. See public privacy policy.

---

## Store wiring (later)

| Store | Purchase type | Bridge |
|-------|---------------|--------|
| Microsoft Store | Paid app or durable add-on | WinRT StoreContext → register with license API |
| Mac App Store | Paid app | StoreKit → same |
| Google Play | One-time product | Play Billing → same |
| iOS | Non-consumable IAP | StoreKit 2 → same |
| Direct | Stripe/Paddle $49.99 | Issues same key |

---

## Client UI

- **Activate license** page (WinUI): status, enter key, optional email, clear, open privacy/site.
- About / Settings link to Activate.
- Home soft banner when `enforce && !licensed` (follow-up).

---

## Pricing note

**$49.99 one-time** is the list price for lifetime personal (`price_usd` / plan `lifetime_personal`).  

| Window | Customer pays (USD, approximate) |
|--------|----------------------------------|
| Through **2026-10-21** | **~$24.99** (50% pre-launch promo in Partner Center) |
| After promo ends | **$49.99** list |

Promo is configured in **Microsoft Store pricing**, not in the license key format.  
Regional / family SKUs can adjust later without changing plan id `lifetime_personal`.

---

## Legal (todo with counsel)

- Dual clarity: MIT source vs commercial store binary / trademark.
- EULA + refunds via each store’s policy.
- Export controls N/A for this app class in typical markets.

---

## Implementation checklist

- [x] Design doc (this file)
- [x] Local `license_service` + `/api/license*`
- [x] WinUI Activate page (stub + real local activate)
- [ ] License Cloudflare Worker
- [ ] Microsoft Store receipt → activate
- [ ] Soft/hard gate in commercial builds (`FOS_LICENSE_ENFORCE=1` in store pipeline)
- [ ] Mac / mobile clients share same API contract
