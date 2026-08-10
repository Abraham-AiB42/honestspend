# Copy a runnable Python engine next to a published WinUI app.
# Layout:
#   dist/HonestSpend.WinUI-win-x64/HonestSpend.WinUI.exe
#   dist/HonestSpend.WinUI-win-x64/engine/   ← this script creates
#     pyproject.toml, src/, .venv/
#
# Usage (from repo root):
#   .\scripts\publish-winui.ps1
#   .\scripts\prepare-engine-bundle.ps1
#   .\scripts\prepare-engine-bundle.ps1 -Target "C:\path\to\publish\folder"

param(
    [string]$Target = "",
    # Also write dist\engine-portable.zip for MSIX first-run extract
    [switch]$ZipPortable
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Target) {
    $Target = Join-Path $Root "dist\HonestSpend.WinUI-win-x64"
}

if (-not (Test-Path $Target)) {
    Write-Error "Publish folder not found: $Target - run publish-winui.ps1 first or pass -Target"
}

$Engine = Join-Path $Target "engine"
Write-Host "Preparing engine bundle -> $Engine"

New-Item -ItemType Directory -Force -Path $Engine | Out-Null

# Lightweight copy: source + project metadata (not full .git)
$robocopy = @(
    @("$Root\src", "$Engine\src", "/E", "/XD", "__pycache__", "*.egg-info", ".pytest_cache"),
    @("$Root\web", "$Engine\web", "/E")
)
foreach ($args in $robocopy) {
    & robocopy @args /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    # robocopy exit 0-7 = success-ish
    if ($LASTEXITCODE -ge 8) { Write-Error "robocopy failed: $args" }
}

Copy-Item "$Root\pyproject.toml" $Engine -Force
if (Test-Path "$Root\README.md") { Copy-Item "$Root\README.md" $Engine -Force }
if (Test-Path "$Root\LICENSE") { Copy-Item "$Root\LICENSE" $Engine -Force }

# Create venv + install package
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Error "python not on PATH" }

Write-Host "Creating .venv in engine..."
& python -m venv "$Engine\.venv"
$pip = Join-Path $Engine ".venv\Scripts\pip.exe"
$python = Join-Path $Engine ".venv\Scripts\python.exe"
& $pip install -U pip
& $pip install -e "$Engine"

# Portable zip for MSIX Store first-run (extracted to %LocalAppData%\HonestSpend\engine)
$doZip = $ZipPortable -or $true
if ($doZip) {
    $dist = Join-Path $Root "dist"
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    $zipPath = Join-Path $dist "engine-portable.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Write-Host "Zipping portable engine -> $zipPath"
    # Zip contents are the engine folder root (src, .venv, pyproject.toml)
    Compress-Archive -Path (Join-Path $Engine "*") -DestinationPath $zipPath -Force
    # Also place next to published EXE for unpackaged + MSIX content copy
    $zipNextToApp = Join-Path $Target "engine-portable.zip"
    Copy-Item $zipPath $zipNextToApp -Force
    Write-Host "  Also: $zipNextToApp"
}

Write-Host ""
Write-Host "Engine ready. WinUI BackendHost looks for:"
Write-Host "  $Engine"
Write-Host "  or %LocalAppData%\HonestSpend\engine (from engine-portable.zip)"
Write-Host "Smoke test:"
Write-Host "  & `"$python`" -m financial_os.cli serve"
Write-Host "Then run HonestSpend.WinUI.exe from $Target"
