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
    # Engine portable zip: first-run extracts to %LocalAppData%\HonestSpend\engine
    $projDir = Join-Path $Root "clients\HonestSpend.WinUI"
    $zipDist = Join-Path $Root "dist\engine-portable.zip"
    $zipInProj = Join-Path $projDir "engine-portable.zip"
    $needEngine = $IncludeEngine -or -not (Test-Path $zipDist)
    if ($IncludeEngine -or (Test-Path $zipDist) -or $true) {
        if (-not (Test-Path $zipDist) -or $IncludeEngine) {
            Write-Host "Preparing engine-portable.zip for Store first-run..." -ForegroundColor Cyan
            $tmp = Join-Path $Root "dist\msix-engine-stage"
            if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $tmp | Out-Null
            # Dummy so prepare-engine-bundle accepts Target
            Set-Content (Join-Path $tmp ".keep") "" -Encoding ASCII
            & (Join-Path $Root "scripts\prepare-engine-bundle.ps1") -Target $tmp
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipDist)) {
                Write-Host "  Engine zip not created - MSIX will be UI-only (Settings can set Backend root)." -ForegroundColor Yellow
            }
        }
        if (Test-Path $zipDist) {
            Copy-Item $zipDist $zipInProj -Force
            Write-Host "  Packaged: engine-portable.zip (first-run extract)" -ForegroundColor Green
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
        Write-Host "  - Or open clients\HonestSpend.WinUI in Visual Studio > Package and Publish"
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
Write-Host "  Sideload:  Add-AppxPackage -Path <file.msix>  (trust CER first if self-signed)"
Write-Host "  Store:     Partner Center > MSIX product > upload package; set runFullTrust notes"
Write-Host "  Docs:      docs\MSIX.md"
Write-Host "Done."
