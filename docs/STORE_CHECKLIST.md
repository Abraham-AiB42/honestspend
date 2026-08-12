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
  - `Identity Name=` → `AgencyinBox42.HonestSpend`  
  - `Identity Publisher=` → `CN=AA975026-9A8A-4BD4-9A1E-623BC6D70EE3`  
  - `PublisherDisplayName=` → **Agency in Box 42**  
- [ ] Bump `Identity Version=` only after a package was accepted (don’t thrash versions on rejected uploads)  
- [ ] **Device family:** package is Desktop x64 only — uncheck Xbox / other families in the submission

## B. Build package (repo)

```powershell
# From repo root — needs Python + .NET 10 SDK
.\scripts\sync-version.ps1          # optional: align all version files
.\scripts\package-msix.ps1          # builds engine-portable.zip + unpacked engine\ + .msix
# Output: dist\msix\**\*.msix
```

- [ ] Confirm `engine-portable.zip` was included (script logs “Packaged: engine-portable.zip”)  
- [ ] Confirm unpacked runtime (script logs “Packaged loose python314.dll”)  
- [ ] Sideload test on a clean PC or VM:  
  `Add-AppxPackage -Path <msix>`  
  First launch must **not** show “python314.dll was not found”.  
  Engine is package-local `engine\python\python.exe`.  
  Home should reach Safe to spend (not permanent offline)

## C. Submission content

- [ ] **Packages:** upload `.msix`  
- [ ] **Properties:** category Finance; privacy URL → hosted [PRIVACY.md](./PRIVACY.md)  
- [ ] **Age ratings:** complete questionnaire  
- [ ] **Store listing:** paste from [STORE_LISTING.md](./STORE_LISTING.md)  
- [ ] **Store listing logos:** use `store-assets/logo/icon-300x300.png` (and 150/71) — same mark as package tiles  
- [ ] **Screenshots:** follow STORE_LISTING checklist (4+) — real HonestSpend UI only, no other apps  
- [ ] **Submission options → Restricted capabilities:** paste  
  `packaging/msix/Notes-for-certification.txt` for `runFullTrust`  
- [ ] **Notes for certification:** first-run path (Get started, no login required)  

### Prior cert failures (see support-file analysis)

Full write-up: **[STORE_CERT_ANALYSIS.md](./STORE_CERT_ANALYSIS.md)**  
Evidence: `HonestSpend_10.1.1.11_screenshot1.png` (Start tile = **X placeholder**), `HonestSpend_crashlog.evtx` (**0xc000027b** in `Microsoft.UI.Xaml.dll`, package **1.0.32.0**).

| Code | What cert saw | Mitigation in repo |
|------|----------------|-------------------|
| **10.1.1.11** | Default broken-tile “X” next to HonestSpend — logos missing at cert DPI | Full scale-100+200 assets; brand navy; wide wordmark; Assets copy to output |
| **Crash** | `Microsoft.UI.Xaml.dll` / `0xc000027b` on launch | Remove TitleBar `ImageIconSource` (XAML load crash); absolute `SetIcon`; soft-fail launch |

**Before every Store upload:**

```powershell
python scripts/generate-store-tiles.py
.\scripts\package-msix.ps1          # fails if engine zip missing
# Sideload on a clean VM (no Python installed):
Add-AppxPackage -Path dist\msix\**\*.msix
# Confirm: window appears <3s; Home or Get started; no python3xx.dll loader dialog;
# engine is package-local engine\python\python.exe (+ python314.dll)
```

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
