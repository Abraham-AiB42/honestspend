# Publish HonestSpend WinUI (self-contained x64) for sideload / local install.
# Requires: .NET 10 SDK, Windows App SDK workloads as used by the project.
#
# Usage (from repo root):
#   .\scripts\publish-winui.ps1
#   .\scripts\publish-winui.ps1 -Configuration Release
#   .\scripts\publish-winui.ps1 -Msix   # if Windows Application Packaging tools available

param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$Msix
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Proj = Join-Path $Root "clients\HonestSpend.WinUI\HonestSpend.WinUI.csproj"
$Out = Join-Path $Root "dist\HonestSpend.WinUI-win-x64"

if (-not (Test-Path $Proj)) {
    Write-Error "Project not found: $Proj"
}

Write-Host "Publishing HonestSpend.WinUI ($Configuration, win-x64) → $Out"
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# Explicit PublishTrimmed=false — WinUI self-contained + trim is fragile (NETSDK1102).
dotnet publish $Proj `
    -c $Configuration `
    -p:Platform=x64 `
    -p:RuntimeIdentifier=win-x64 `
    -p:SelfContained=true `
    -p:PublishReadyToRun=true `
    -p:PublishTrimmed=false `
    -o $Out

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Published app folder:"
Write-Host "  $Out"
Write-Host "Run:  $Out\HonestSpend.WinUI.exe"
Write-Host ""
Write-Host "Bundle the Python engine next to the EXE (recommended):"
Write-Host "  .\scripts\prepare-engine-bundle.ps1 -Target `"$Out`""
Write-Host "Creates $Out\engine with .venv — BackendHost auto-detects it."
Write-Host ""
Write-Host "Or keep using a full repo clone and set Backend root in Settings."

if ($Msix) {
    Write-Host ""
    Write-Host "MSIX: open clients\HonestSpend.WinUI in Visual Studio → Package and Publish → Create App Packages."
    Write-Host "Or: msbuild with WindowsAppSDK packaging targets once signing cert is configured."
    Write-Host "Identity in Package.appxmanifest — update Publisher CN for your cert before store/sideload."
}

Write-Host "Done."
