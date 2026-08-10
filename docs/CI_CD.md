# CI/CD — site + Microsoft Store

Automated pipelines for HonestSpend:

| Pipeline | Workflow | Trigger | What it does |
|----------|----------|---------|--------------|
| **Marketing site** | `deploy-site.yml` | Push to `main` when `site/**` changes; manual | Deploys `site/` → Cloudflare Pages (`honestspend` → honestspend.net) |
| **GitHub release** | `release.yml` | Tag `v*` | Tests + WinUI zip + GitHub Release |
| **Store MSIX** | `store-submit.yml` | Tag `v*` (if configured) or manual | Builds MSIX; optionally submits to Partner Center |

---

## 1. Cloudflare Pages (site auto-deploy)

### Secrets (repo → Settings → Secrets and variables → Actions)

| Name | Value |
|------|--------|
| `CLOUDFLARE_API_TOKEN` | Custom token: **Account → Cloudflare Pages → Edit** |
| `CLOUDFLARE_ACCOUNT_ID` | Account id (Overview → API sidebar, or zone API section) |

### Behavior

- Edits under `site/` pushed to `main` deploy production Pages project **honestspend**.
- Custom domains (`honestspend.net`, `www`) are managed in Cloudflare once; deploys update content only.
- Manual: Actions → **Deploy site (Cloudflare Pages)** → Run workflow.

### Local equivalent

```powershell
$env:CLOUDFLARE_API_TOKEN = "…"
$env:CLOUDFLARE_ACCOUNT_ID = "…"
npx wrangler pages deploy site --project-name=honestspend --branch=main
```

Privacy URL for Store listing: **https://honestspend.net/privacy/**

---

## 2. Microsoft Store (package auto-submit)

Store automation is **possible** via the [Microsoft Store Developer CLI](https://learn.microsoft.com/en-us/windows/apps/publish/msstore-dev-cli/github-actions) and GitHub Actions. It is **not** fully turnkey until Partner Center + Entra are linked.

### Limits (honest expectations)

| Can automate | Still manual / external |
|--------------|-------------------------|
| Build MSIX on tag / dispatch | First app creation + name reservation |
| Upload package + start submission (free apps) | Age ratings questionnaire (first time) |
| Subsequent package updates after go-live | Screenshot / listing polish if required |
| | Microsoft **certification** review |
| | Manifest **Name** + **Publisher** must match Partner Center identity |

Microsoft notes: package/metadata auto-update via this CLI path currently targets **free** products.

### One-time Partner Center + Entra setup

1. [Register as a Windows developer](https://partner.microsoft.com/dashboard) and create the **HonestSpend** app as **MSIX**.
2. Associate or create a **Microsoft Entra** tenant on the Partner Center account.
3. Register an app in Entra (App registrations) → create a **client secret**.
4. Partner Center → Account settings → User management → **Microsoft Entra applications** → add that app with **Manager** role.
5. Copy **Product identity** (Package/Identity Name + Publisher CN) into  
   `clients/HonestSpend.WinUI/Package.appxmanifest` (must match every Store package).
6. Complete **first** listing manually (privacy URL, screenshots, age ratings, notes for `runFullTrust`) and publish at least once if required by your account state.
7. Note **Seller / Publisher ID** and **Store product ID** (app identity in Partner Center).

### GitHub secrets

| Secret | Source |
|--------|--------|
| `AZURE_AD_TENANT_ID` | Entra → Overview → Tenant ID |
| `AZURE_AD_APPLICATION_CLIENT_ID` | Entra app → Application (client) ID |
| `AZURE_AD_APPLICATION_SECRET` | Entra app → Certificates & secrets |
| `SELLER_ID` | Partner Center → Account settings → identifiers (Seller/Publisher ID) |

### GitHub variable

| Variable | Source |
|----------|--------|
| `STORE_PRODUCT_ID` | Partner Center product / Store ID for HonestSpend |

Settings → Secrets and variables → Actions → **Variables** → New repository variable.

### How to ship a Store update

1. Bump version in `Package.appxmanifest` (and app version files via `scripts/sync-version.ps1` if used).
2. Commit, tag, push:

```powershell
git tag v1.0.32
git push origin v1.0.32
```

3. Workflow **Store MSIX (build / submit)**:
   - Always **builds** the MSIX and uploads a GitHub Actions artifact.
   - **Submits** to Partner Center when `STORE_PRODUCT_ID` is set and secrets exist (tag push), or when you run workflow_dispatch with **submit = true**.

4. Watch Partner Center for certification.

### Manual run (build only)

Actions → **Store MSIX (build / submit)** → Run workflow → leave **submit** unchecked → download artifact.

### Manual run (build + submit)

Same workflow → **submit = true** (requires secrets + `STORE_PRODUCT_ID`).

### CLI smoke test (your machine)

```powershell
# After installing msstore CLI / using microsoft-store-apppublisher action locally:
msstore reconfigure --tenantId … --sellerId … --clientId … --clientSecret …
msstore publish path\to\HonestSpend.msix -id <STORE_PRODUCT_ID>
```

---

## 3. Recommended release flow

```
code change → CI tests (ci.yml)
           → site/** change → deploy-site.yml → honestspend.net
version bump + tag vX.Y.Z → release.yml → GitHub Release (zip)
                         → store-submit.yml → MSIX artifact + Store submit (if configured)
```

---

## 4. Security notes

- Never commit API tokens or client secrets.
- Rotate any token that was pasted into chat or logs.
- Prefer least privilege: Cloudflare token = Pages Edit only; Entra app = Partner Center Manager only for that publisher.
