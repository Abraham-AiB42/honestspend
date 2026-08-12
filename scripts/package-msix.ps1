# Build HonestSpend as MSIX (sideload or Store-prep).
#
# Usage (from repo root):
#   .\scripts\package-msix.ps1
#   .\scripts\package-msix.ps1 -CreateSelfSignedCert   # first-time local cert
#   .\scripts\package-msix.ps1 -IncludeEngine           # stage engine\ into package layout
#   .\scripts\package-msix.ps1 -Configuration Debug
#
# Output:
#   dist\msix\  - .msix / package layout
#   packaging\msix\HonestSpend-Dev.pfx  - when -CreateSelfSignedCert
#
# Partner Center:
#   1. Create product as MSIX package type
#   2. Copy Package/Identity Name + Publisher into Package.appxmanifest
#   3. Submit the .msix / .msixupload from this script (or VS Package and Publish)
#   4. See docs\MSIX.md

param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$CreateSelfSignedCert,
    [switch]$IncludeEngine,
    [switch]$SkipBuild,
    # Store submission MUST ship engine-portable.zip (unpackaged repair). Only skip for UI-only experiments.
    [switch]$AllowNoEngine,
    [string]$Publisher = "CN=HonestSpend Dev",
    [string]$CertPassword = "HonestSpend-Dev-Only"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Proj = Join-Path $Root "clients\HonestSpend.WinUI\HonestSpend.WinUI.csproj"
$OutDir = Join-Path $Root "dist\msix"
$CertDir = Join-Path $Root "packaging\msix"
$Pfx = Join-Path $CertDir "HonestSpend-Dev.pfx"
$Cer = Join-Path $CertDir "HonestSpend-Dev.cer"

if (-not (Test-Path $Proj)) {
    Write-Error "Project not found: $Proj"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path $CertDir | Out-Null

# Drop previous package folders so Partner Center uploads don't mix old identities
if (Test-Path $OutDir) {
    Get-ChildItem $OutDir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "Removing old package output: $($_.Name)" -ForegroundColor DarkGray
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "=== HonestSpend MSIX package ($Configuration) ===" -ForegroundColor Cyan

# Optional self-signed cert for local sideload (NOT for Store submission)
if ($CreateSelfSignedCert) {
    Write-Host "Creating self-signed cert for local sideload..." -ForegroundColor Cyan
    if (Test-Path $Pfx) {
        Write-Host "  Existing PFX kept: $Pfx" -ForegroundColor Yellow
    }
    else {
        $cert = New-SelfSignedCertificate `
            -Type Custom `
            -Subject $Publisher `
            -KeyUsage DigitalSignature `
            -FriendlyName "HonestSpend MSIX Dev" `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
        $sec = ConvertTo-SecureString -String $CertPassword -Force -AsPlainText
        Export-PfxCertificate -Cert $cert -FilePath $Pfx -Password $sec | Out-Null
        Export-Certificate -Cert $cert -FilePath $Cer | Out-Null
        Write-Host "  PFX: $Pfx" -ForegroundColor Green
        Write-Host "  CER: $Cer (install to Trusted People for sideload)" -ForegroundColor Green
        Write-Host "  Password: $CertPassword" -ForegroundColor Yellow
    }
}

$signArgs = @()
if (Test-Path $Pfx) {
    $signArgs = @(
        "-p:AppxPackageSigningEnabled=true",
        "-p:PackageCertificateKeyFile=$Pfx",
        "-p:PackageCertificatePassword=$CertPassword"
    )
    Write-Host "Signing with: $Pfx" -ForegroundColor Cyan
}
else {
    Write-Host "No packaging\msix\*.pfx - building unsigned package layout (VS/Store can sign)." -ForegroundColor Yellow
    $signArgs = @("-p:AppxPackageSigningEnabled=false")
}

if (-not $SkipBuild) {
    # Engine portable zip: unpackaged repair payload. Store launch uses package-local engine\python\.
    $projDir = Join-Path $Root "clients\HonestSpend.WinUI"
    $zipDist = Join-Path $Root "dist\engine-portable.zip"
    $zipInProj = Join-Path $projDir "engine-portable.zip"
    Write-Host "Preparing engine-portable.zip (unpackaged repair) + unpacked engine\..." -ForegroundColor Cyan
    if (-not (Test-Path $zipDist) -or $IncludeEngine) {
        $tmp = Join-Path $Root "dist\msix-engine-stage"
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        Set-Content (Join-Path $tmp ".keep") "" -Encoding ASCII
        & (Join-Path $Root "scripts\prepare-engine-bundle.ps1") -Target $tmp
    }
    if (-not (Test-Path $zipDist)) {
        if ($AllowNoEngine) {
            Write-Host "  WARNING: no engine-portable.zip (-AllowNoEngine). Store will likely fail cert." -ForegroundColor Yellow
        }
        else {
            Write-Error "engine-portable.zip missing at $zipDist. Store packages must include the engine. Fix prepare-engine-bundle.ps1 or pass -AllowNoEngine for UI-only experiments."
        }
    }
    else {
        Copy-Item $zipDist $zipInProj -Force
        $zipMb = [math]::Round((Get-Item $zipInProj).Length / 1MB, 1)
        Write-Host "  Packaged: engine-portable.zip ($zipMb MB) - unpackaged repair payload" -ForegroundColor Green
    }

    # Ship unpacked engine\python\ + versioned python3xx.dll inside the MSIX.
    $engineInProj = Join-Path $projDir "engine"
    $RequiredDll = "python314.dll"
    function Test-VersionedDllName([string]$name) {
        return $name -match '^python3[0-9]+\.dll$'
    }
    function Get-EmbedDll([string]$engineRoot) {
        Get-ChildItem (Join-Path $engineRoot "python") -Filter "python3*.dll" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Length -gt 0 -and (Test-VersionedDllName $_.Name) } |
            Select-Object -First 1
    }
    function Test-EngineReady([string]$engineRoot) {
        $dll = Get-EmbedDll $engineRoot
        if (-not $dll -or $dll.Name -ne $RequiredDll) { return $false }
        $pkg = Join-Path $engineRoot "python\Lib\site-packages\honestspend"
        $src = Join-Path $engineRoot "src\honestspend"
        return (Test-Path $pkg) -or (Test-Path $src)
    }
    $srcEngine = @(
        (Join-Path $Root "dist\msix-engine-stage\engine"),
        (Join-Path $Root "dist\engine-stage\engine"),
        (Join-Path $Root "dist\HonestSpend-Windows-x64\engine")
    ) | Where-Object { Test-EngineReady $_ } | Select-Object -First 1

    if ($srcEngine) {
        Write-Host "  Staging unpacked engine\ into WinUI project from $srcEngine ..." -ForegroundColor Cyan
        if (Test-Path $engineInProj) { Remove-Item $engineInProj -Recurse -Force }
        Copy-Item $srcEngine $engineInProj -Recurse -Force
    }
    elseif ((Test-Path $zipDist) -and -not (Test-EngineReady $engineInProj)) {
        Write-Host "  Unpacking engine-portable.zip into WinUI engine\ ..." -ForegroundColor Cyan
        if (Test-Path $engineInProj) { Remove-Item $engineInProj -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $engineInProj | Out-Null
        Expand-Archive -Path $zipDist -DestinationPath $engineInProj -Force
    }

    $needRebuild = -not (Test-EngineReady $engineInProj)
    $embedPy = Join-Path $engineInProj "python\python.exe"
    if (-not $needRebuild -and (Test-Path $embedPy)) {
        & $embedPy -c "import honestspend"
        if ($LASTEXITCODE -ne 0) { $needRebuild = $true }
    }

    if ($needRebuild) {
        if ($AllowNoEngine) {
            Write-Host "  WARNING: $RequiredDll / honestspend not staged (-AllowNoEngine)." -ForegroundColor Yellow
        }
        else {
            Write-Host "  Staged engine is stale or incomplete. Rebuilding $RequiredDll bundle..." -ForegroundColor Yellow
            $tmp = Join-Path $Root "dist\msix-engine-stage"
            if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $tmp | Out-Null
            & (Join-Path $Root "scripts\prepare-engine-bundle.ps1") -Target $tmp
            $rebuilt = Join-Path $tmp "engine"
            if (-not (Test-EngineReady $rebuilt)) {
                Write-Error "Rebuild did not produce $RequiredDll + honestspend"
            }
            if (Test-Path $engineInProj) { Remove-Item $engineInProj -Recurse -Force }
            Copy-Item $rebuilt $engineInProj -Recurse -Force
            if (Test-Path (Join-Path $Root "dist\engine-portable.zip")) {
                Copy-Item (Join-Path $Root "dist\engine-portable.zip") $zipInProj -Force
            }
            & $embedPy -c "import honestspend"
            if ($LASTEXITCODE -ne 0) {
                Write-Error "Rebuilt engine still cannot import honestspend"
            }
        }
    }

    $dllInProj = Get-EmbedDll $engineInProj
    if ($dllInProj) {
        $dllMb = [math]::Round($dllInProj.Length / 1MB, 1)
        Write-Host "  Packaged loose $($dllInProj.Name) ($dllMb MB) next to python.exe" -ForegroundColor Green
        Write-Host "  Staged engine imports honestspend" -ForegroundColor Green
    }

    # Refresh branded tiles before package (Store 10.1.1.11)
    $tileScript = Join-Path $Root "scripts\generate-store-tiles.py"
    if (Test-Path $tileScript) {
        Write-Host "Refreshing Store tile assets..." -ForegroundColor Cyan
        try {
            & python $tileScript 2>$null
            if ($LASTEXITCODE -ne 0) {
                & (Join-Path $Root ".venv\Scripts\python.exe") $tileScript
            }
        }
        catch {
            Write-Host "  Tile regen skipped (install Pillow if needed): $_" -ForegroundColor DarkGray
        }
    }

    Write-Host "Building MSIX (WindowsPackageType=MSIX)..." -ForegroundColor Cyan
    $msbuildArgs = @(
        $Proj,
        "-c", $Configuration,
        "-p:Platform=x64",
        "-p:RuntimeIdentifier=win-x64",
        "-p:WindowsPackageType=MSIX",
        "-p:WindowsAppSDKSelfContained=true",
        "-p:SelfContained=true",
        "-p:PublishTrimmed=false",
        "-p:GenerateAppxPackageOnBuild=true",
        "-p:AppxPackageDir=$OutDir\",
        "-p:UapAppxPackageBuildMode=SideloadOnly",
        "-p:AppxBundle=Never"
    ) + $signArgs

    & dotnet build @msbuildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "dotnet build MSIX failed. Fallback notes:" -ForegroundColor Yellow
        Write-Host "  - Install Single-project MSIX Packaging Tools / Windows SDK makeappx"
        Write-Host "  - Or open clients\HonestSpend.WinUI in Visual Studio, Package and Publish"
        Write-Host "  - See docs\MSIX.md"
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "MSIX output folder: $OutDir" -ForegroundColor Green
Get-ChildItem $OutDir -Recurse -Include *.msix,*.msixbundle,*.appx,*.appxbundle -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "  $($_.FullName)" }

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  Sideload:  Add-AppxPackage -Path path\to\file.msix  (trust CER first if self-signed)"
Write-Host "  Store:     Partner Center, MSIX product, upload package; set runFullTrust notes"
Write-Host "  Docs:      docs\MSIX.md"
Write-Host "Done."
