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

1. **New product → package type: MSIX** (sticky — choose carefully).  
2. **Reserve name** e.g. HonestSpend.  
3. **Product identity** → copy **Package/Identity Name** and **Publisher** into `Package.appxmanifest`.  
4. Upload package from `dist\msix` or VS.  
5. **Submission options → Restricted capabilities**: paste notes from  
   `packaging/msix/Notes-for-certification.txt` for `runFullTrust`.  
6. Privacy policy URL if you collect personal data (local-only defaults still often need a policy page).  
7. Screenshots + listing (Simple Home, Import, Safe to spend).  

## Engine strategy (hybrid app)

The UI **must** spawn a local Python engine (`BackendHost`). Options:

### A — Recommended for first Store submission (smaller package)

1. MSIX contains **WinUI only** (self-contained).  
2. On first run, extract or download **engine** under  
   `%LocalAppData%\HonestSpend\engine\` (or document “Settings → Backend root”).  
3. `BackendHost` already searches for `engine\` next to the EXE and configurable roots —  
   extend paths later if package install path differs.

### B — Fat package (`-IncludeEngine`)

1. `prepare-engine-bundle.ps1` stages `engine\` into the project.  
2. MSIX Content includes `engine\**` (wire `Content` Include when ready — venv is large).  
3. Certification size + review time grow; still needs `runFullTrust` to execute Python.

Scaffold implements **A** by default; **B** is opt-in and incomplete until you add:

```xml
<!-- Example — only after staging engine\ -->
<ItemGroup>
  <Content Include="engine\**\*" CopyToOutputDirectory="PreserveNewest"
           Exclude="engine\**\__pycache__\**;engine\**\*.pyc" />
</ItemGroup>
```

## Identity placeholders

Edit `Package.appxmanifest` before Store:

| Attribute | Scaffold default | Replace with |
|-----------|------------------|--------------|
| `Identity@Name` | `HonestSpend.HonestSpend` | Partner Center package family name prefix |
| `Identity@Publisher` | `CN=HonestSpend Dev` | Partner Center publisher ID / cert CN |
| `Identity@Version` | `1.0.31.0` | Each submission (must increase) |

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
