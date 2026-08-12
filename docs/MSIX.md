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

## Engine strategy (self-contained — users do **not** need Python)

The UI is WinUI; the fiscal engine is Python. **End users never install Python.**

### What ships

`prepare-engine-bundle.ps1` builds a **relocatable** tree:

| Piece | Role |
|-------|------|
| `engine\python\` | Official **CPython embeddable** (x64) + pip + all wheels |
| `engine\src\` + installed package | HonestSpend code in `site-packages` |
| `dist\engine-portable.zip` | Same tree, zipped for Store first-run |

A developer machine needs Python **only to build** the bundle (download embeddable + `pip install`).  
A customer PC only needs the WinUI EXE + that zip.

### Default Store path (automatic)

1. `package-msix.ps1` runs the bundle script, embeds **engine-portable.zip**, and
   stages unpacked **`engine\python\`** (including **python314.dll**) into the MSIX.  
2. `BackendHost` starts the **package-local** `engine\python\python.exe` (not system
   `python`, not WindowsApps).  
3. LocalAppData extract is **unpackaged zip only**. A packaged child cannot load
   `python314.dll` from LocalAppData — Store 10.1.2.10.  
4. Settings → **Install / repair engine** re-points at package-local `engine\python\`.  

Still requires **runFullTrust** to spawn that private `python.exe` and write under LocalAppData / `.HonestSpend`.

### Unpackaged GitHub zip

`package-release.ps1` keeps `engine\` next to the EXE (and writes `engine-portable.zip`).

## Identity placeholders

Edit `Package.appxmanifest` before Store:

| Attribute | Scaffold default | Replace with |
|-----------|------------------|--------------|
| `Identity@Name` | `AgencyinBox42.HonestSpend` | Partner Center package/identity name |
| `Identity@Publisher` | `CN=AA975026-9A8A-4BD4-9A1E-623BC6D70EE3` | Partner Center publisher CN |
| `Identity@Version` | e.g. `1.0.32.0` | Bump only after a package is accepted / live |
| `PublisherDisplayName` | **Agency in Box 42** | Partner Center publisher display name |

Common rejections:

- `PublisherDisplayName` = app name instead of publisher display name  
- `Identity@Name` / `Publisher` still set to local sideload placeholders  

**Device families:** this package is **Windows.Desktop** only (x64). In Partner Center submission, uncheck Xbox / HoloLens / Surface Hub / etc. unless you ship packages for those families.

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
