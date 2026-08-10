# Partner Center checklist (HonestSpend MSIX)

Do these in order. Product type must be **MSIX** (not EXE/MSI).

## A. Create product (you — Partner Center)

- [ ] Partner Center → **Windows** → **Create a new app**  
- [ ] Package type: **MSIX**  
- [ ] Reserve name: **HonestSpend** (or final public name)  
- [ ] Open **Product identity** → copy:  
  - Package/Identity **Name**  
  - **Publisher** (CN=…)  
- [ ] Paste into `clients/HonestSpend.WinUI/Package.appxmanifest`  
  - `Identity Name=`  
  - `Identity Publisher=` (must match Partner Center CN exactly)  
  - `PublisherDisplayName=` must match **publisher display name** exactly  
    (this account: **Agency in Box 42** — not the app name HonestSpend)  
- [ ] Bump `Identity Version=` for every submission (e.g. `1.0.32.0` → `1.0.33.0`)

## B. Build package (repo)

```powershell
# From repo root — needs Python + .NET 10 SDK
.\scripts\sync-version.ps1          # optional: align all version files
.\scripts\package-msix.ps1          # builds engine-portable.zip + .msix
# Output: dist\msix\**\*.msix
```

- [ ] Confirm `engine-portable.zip` was included (script logs “Packaged: engine-portable.zip”)  
- [ ] Sideload test on a clean PC or VM:  
  `Add-AppxPackage -Path <msix>`  
  First launch should extract engine to `%LocalAppData%\HonestSpend\engine`  
  Home should reach Safe to spend (not permanent offline)

## C. Submission content

- [ ] **Packages:** upload `.msix`  
- [ ] **Properties:** category Finance; privacy URL → hosted [PRIVACY.md](./PRIVACY.md)  
- [ ] **Age ratings:** complete questionnaire  
- [ ] **Store listing:** paste from [STORE_LISTING.md](./STORE_LISTING.md)  
- [ ] **Screenshots:** follow STORE_LISTING checklist (4+)  
- [ ] **Submission options → Restricted capabilities:** paste  
  `packaging/msix/Notes-for-certification.txt` for `runFullTrust`  
- [ ] **Notes for certification:** first-run path (Get started, no login required)

## D. Dual channel (keep)

| Channel | Artifact |
|---------|----------|
| **Microsoft Store** | MSIX from `package-msix.ps1` |
| **GitHub Releases** | Zip from `package-release.ps1` (+ optional Inno) |

## E. After certify

- [ ] Install from Store on a clean machine  
- [ ] Import a real bank CSV once  
- [ ] Confirm set books / Sort / month-close  
- [ ] File any Store review feedback as GitHub issues  

## F. CI/CD (after first live product)

Automated MSIX build + Partner Center submit is wired in  
`.github/workflows/store-submit.yml`. Setup secrets/vars and limits: **[CI_CD.md](./CI_CD.md)**.

Marketing site (privacy URL host) auto-deploys via `deploy-site.yml` → Cloudflare Pages.

## Related

- [CI_CD.md](./CI_CD.md) — GitHub Actions: site + Store  
- [MSIX.md](./MSIX.md) — packaging architecture  
- [PRIVACY.md](./PRIVACY.md)  
- [STORE_LISTING.md](./STORE_LISTING.md)  
