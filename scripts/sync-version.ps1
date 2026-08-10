# Align version strings across engine, Inno, MSIX manifest, and WinUI project.
#
# Usage:
#   .\scripts\sync-version.ps1                 # read from pyproject.toml
#   .\scripts\sync-version.ps1 -Version 1.0.32

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $Version) {
    $py = Get-Content (Join-Path $Root "pyproject.toml") -Raw
    if ($py -match 'version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
        $Version = $Matches[1]
    }
    else {
        Write-Error "Could not read version from pyproject.toml"
    }
}

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    Write-Error "Version must be x.y.z (got $Version)"
}

$msixVer = "$Version.0"
Write-Host "Syncing version $Version (MSIX $msixVer)" -ForegroundColor Cyan

# pyproject already source of truth if -Version omitted; still rewrite if provided
$pyPath = Join-Path $Root "pyproject.toml"
$pyText = Get-Content $pyPath -Raw
$pyText = $pyText -replace 'version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', "version = `"$Version`""
Set-Content $pyPath $pyText -NoNewline -Encoding utf8

$init = Join-Path $Root "src\financial_os\__init__.py"
$initText = Get-Content $init -Raw
$initText = $initText -replace '__version__\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', "__version__ = `"$Version`""
Set-Content $init $initText -NoNewline -Encoding utf8

$iss = Join-Path $Root "packaging\HonestSpend.iss"
if (Test-Path $iss) {
    $issText = Get-Content $iss -Raw
    $issText = $issText -replace '#define MyAppVersion "[0-9]+\.[0-9]+\.[0-9]+"', "#define MyAppVersion `"$Version`""
    Set-Content $iss $issText -NoNewline -Encoding utf8
}

$manifest = Join-Path $Root "clients\HonestSpend.WinUI\Package.appxmanifest"
if (Test-Path $manifest) {
    $m = Get-Content $manifest -Raw
    $m = $m -replace 'Version="[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"', "Version=`"$msixVer`""
    Set-Content $manifest $m -NoNewline -Encoding utf8
}

$csproj = Join-Path $Root "clients\HonestSpend.WinUI\HonestSpend.WinUI.csproj"
if (Test-Path $csproj) {
    $c = Get-Content $csproj -Raw
    $c = $c -replace '<ApplicationDisplayVersion>[0-9.]+</ApplicationDisplayVersion>',
        "<ApplicationDisplayVersion>$Version</ApplicationDisplayVersion>"
    $c = $c -replace '<ApplicationVersion>[0-9.]+</ApplicationVersion>',
        "<ApplicationVersion>$msixVer</ApplicationVersion>"
    Set-Content $csproj $c -NoNewline -Encoding utf8
}

$verify = Join-Path $Root "scripts\verify-grade-a.ps1"
if (Test-Path $verify) {
    $v = Get-Content $verify -Raw
    # Keep pattern in sync: 1\.0\.NN
    $escaped = [regex]::Escape($Version) -replace '\\', '\'
    # Simpler: replace known version numbers in throws
    $v = $v -replace "1\\\.0\\\.[0-9]+", ($Version -replace '\.', '\.')
    $v = $v -replace "1\.0\.[0-9]+", $Version
    Set-Content $verify $v -NoNewline -Encoding utf8
}

Write-Host "Updated: pyproject, __init__, Inno, Package.appxmanifest, csproj, verify-grade-a (best effort)"
Write-Host "Done. Next: .\scripts\package-msix.ps1"
