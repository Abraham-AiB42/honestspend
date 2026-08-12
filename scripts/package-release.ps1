# Build a distributable Windows folder + zip:
#   dist/HonestSpend-Windows-x64/
#     HonestSpend.WinUI.exe + deps
#     engine/   (Python venv + package)
#     README-INSTALL.txt
#   dist/HonestSpend-Windows-x64.zip
#
# Usage (from repo root):
#   .\scripts\package-release.ps1
#   .\scripts\package-release.ps1 -SkipEngine   # UI only
#   .\scripts\package-release.ps1 -Configuration Debug

param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$SkipEngine,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $Root "dist\HonestSpend-Windows-x64"
$WinUiOut = Join-Path $Root "dist\HonestSpend.WinUI-win-x64"
$Zip = Join-Path $Root "dist\HonestSpend-Windows-x64.zip"

Write-Host "=== HonestSpend package-release ($Configuration) ===" -ForegroundColor Cyan

# 1) Publish WinUI
& (Join-Path $Root "scripts\publish-winui.ps1") -Configuration $Configuration
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 2) Stage clean folder
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item -Path (Join-Path $WinUiOut "*") -Destination $Stage -Recurse -Force

# 3) Engine bundle
if (-not $SkipEngine) {
    Write-Host "Bundling Python engine into stage\engine …" -ForegroundColor Cyan
    & (Join-Path $Root "scripts\prepare-engine-bundle.ps1") -Target $Stage
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "SkipEngine: UI only (set Backend root to a repo clone)." -ForegroundColor Yellow
}

# 4) Install notes
$notes = @"
HonestSpend — Windows package (1.0.56)
=====================================

ONE-CLICK USE
1. Unzip this folder anywhere (keep engine\ next to the EXE)
2. Run HonestSpend.WinUI.exe
3. 2-minute Get started → Safe to spend on Home
4. Import CSV/OFX (or optional BYOK bank link)

DAILY (open rarely)
- Simple mode (default): Home · Add · Can I buy? · Sort charges
- Import → Set Safe to spend from bank when ending bal differs
- Close the month when the checklist is clear
- Full books toggle for ledgers / tax / reconcile depth

ENGINE
- .\engine\ is auto-detected (no separate Python install if packaged here)
- If Home says offline: Settings → Start engine

DATA
- %USERPROFILE%\.HonestSpend  (or Settings → data folder / OneDrive)

Docs: INSTALL.md · CLIENT_FIRST.md · CHANGELOG.md

Built: $(Get-Date -Format o)
"@
Set-Content -Path (Join-Path $Stage "README-INSTALL.txt") -Value $notes -Encoding UTF8

# Copy key docs if present (current product docs — not historical RELEASE_0.x notes)
foreach ($doc in @("docs\INSTALL.md", "docs\SIMPLE_MODE.md", "docs\PRIVACY.md", "docs\AUTOMATION.md", "LICENSE", "CHANGELOG.md")) {
    $src = Join-Path $Root $doc
    if (Test-Path $src) {
        $destName = Split-Path $doc -Leaf
        Copy-Item $src (Join-Path $Stage $destName) -Force
    }
}

# 5) Zip
if (-not $SkipZip) {
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip -Force
    Write-Host "Zip: $Zip" -ForegroundColor Green
}

Write-Host ""
Write-Host "Stage folder: $Stage" -ForegroundColor Green
Write-Host "Run: $Stage\HonestSpend.WinUI.exe"
Write-Host "Inno Setup (optional): compile packaging\HonestSpend.iss after this step."
Write-Host "Done."
