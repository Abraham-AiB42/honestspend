# MSIX packaging (Microsoft Store track)

HonestSpend’s **Store product type should be MSIX** (Desktop Bridge / full-trust WinUI).  
GitHub continues to ship the **unpackaged** zip + optional Inno EXE for sideload.

## Why MSIX for Store

| | MSIX (Store) | EXE/MSI (Store) |
|--|--------------|-----------------|
| Your codesign cert | Not required for Store distribution (Microsoft re-signs) | Required Authenticode (~\$/yr) |
| Matches Store updates | Yes | You own more of install/update |
| Fits hybrid Python engine | Needs full trust + layout work | Easier with Inno today |

Scaffold default: **register the Partner Center product as MSIX**.

## Layout (scaffold)

```
clients/HonestSpend.WinUI/
  Package.appxmanifest     ← identity + runFullTrust
  HonestSpend.WinUI.csproj ← EnableMsixTooling, WindowsPackageType
packaging/msix/
  Notes-for-certification.txt
  HonestSpend-Dev.pfx      ← local only (gitignored pattern)
scripts/package-msix.ps1
docs/MSIX.md               ← this file
```

## Build (local sideload)

```powershell
# From repo root — first time: create self-signed cert
.\scripts\package-msix.ps1 -CreateSelfSignedCert

# Subsequent builds
.\scripts\package-msix.ps1

# Optional: attempt to bake engine\ into package (large; see below)
.\scripts\package-msix.ps1 -IncludeEngine
```

Output: `dist\msix\` (look for `.msix`).

**Trust the CER once** (Current User → Trusted People) before `Add-AppxPackage` for self-signed packages.

## Visual Studio path

1. Open `clients\HonestSpend.WinUI\HonestSpend.WinUI.csproj`  
2. **Package and Publish → Create App Packages…**  
3. Sideload or Store upload as guided  
4. Align **Publisher CN** with Partner Center identity / cert  

## Partner Center checklist

Full interactive checklist: **[STORE_CHECKLIST.md](./STORE_CHECKLIST.md)**  
Listing copy: **[STORE_LISTING.md](./STORE_LISTING.md)** · Privacy: **[PRIVACY.md](./PRIVACY.md)**

1. **New product → package type: MSIX** (sticky — choose carefully).  
2. **Reserve name** e.g. HonestSpend.  
3. **Product identity** → copy **Package/Identity Name** and **Publisher** into `Package.appxmanifest`.  
4. Upload package from `dist\msix` or VS.  
5. **Submission options → Restricted capabilities**: paste notes from  
   `packaging/msix/Notes-for-certification.txt` for `runFullTrust`.  
6. Privacy policy URL → host PRIVACY.md publicly.  
7. Screenshots + listing from STORE_LISTING.md.  

## Engine strategy (hybrid app) — implemented

The UI spawns a local Python engine (`BackendHost` + `EngineBootstrap`).

### Default Store path (automatic)

1. `package-msix.ps1` builds **engine-portable.zip** (via `prepare-engine-bundle.ps1`) and includes it as package content.  
2. On first `EnsureRunning`, **EngineBootstrap** extracts the zip to  
   **`%LocalAppData%\HonestSpend\engine\`**.  
3. Settings → **Install / repair engine** re-runs extract if needed.  

Still requires **runFullTrust** to execute `python.exe` from that folder.

### Unpackaged GitHub zip

`package-release.ps1` keeps `engine\` next to the EXE (and also writes `engine-portable.zip`).

## Identity placeholders

Edit `Package.appxmanifest` before Store:

| Attribute | Scaffold default | Replace with |
|-----------|------------------|--------------|
| `Identity@Name` | `HonestSpend.HonestSpend` | Partner Center package family name prefix |
| `Identity@Publisher` | Partner Center **Publisher** CN | Must match Product identity (not display name) |
| `Identity@Version` | e.g. `1.0.32.0` | Each submission (must increase) |
| `PublisherDisplayName` | **Agency in Box 42** | Must match Partner Center **publisher display name** exactly |

Common rejection: `PublisherDisplayName` set to the app name (`HonestSpend`) instead of the account publisher name.

## Unpackaged vs packaged debug

| Mode | How |
|------|-----|
| Daily dev (unpackaged) | `dotnet run` / `scripts\start-winui.ps1` — `WindowsPackageType=None` default |
| MSIX build | `package-msix.ps1` sets `WindowsPackageType=MSIX` |
| One-folder GitHub ship | `package-release.ps1` + optional Inno (unchanged) |

## Related

- [INSTALL.md](./INSTALL.md) — unpackaged one-folder + Inno  
- [CLIENT_FIRST.md](./CLIENT_FIRST.md) — WinUI primary  
- `scripts/package-release.ps1` — GitHub zip (not Store)  
