# Build a SELF-CONTAINED Python engine for end users (no system Python required).
#
# Layout written to -Target\engine\ (or a staging folder):
#   engine\
#     python\              â† CPython embeddable + pip + site-packages
#       python.exe
#       Lib\site-packages\â€¦
#     src\financial_os\    â† app code (also installed into site-packages)
#     pyproject.toml
#     README_ENGINE.txt
#
# Also writes dist\engine-portable.zip for MSIX first-run extract
#   â†’ %LocalAppData%\HonestSpend\engine\
#
# Usage (from repo root):
#   .\scripts\prepare-engine-bundle.ps1
#   .\scripts\prepare-engine-bundle.ps1 -Target "C:\path\to\publish\folder"
#   .\scripts\prepare-engine-bundle.ps1 -PythonVersion 3.12.8
#
# Build machine still needs network once to download embeddable + wheels.
# End-user machines need ZERO Python install.

param(
    [string]$Target = "",
    [string]$PythonVersion = "3.12.8",
    [switch]$SkipZip,
    [switch]$ForceRedownload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Target) {
    $Target = Join-Path $Root "dist\HonestSpend.WinUI-win-x64"
}

# Allow MSIX staging that only has a placeholder
if (-not (Test-Path $Target)) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
}

$Engine = Join-Path $Target "engine"
$PyHome = Join-Path $Engine "python"
$CacheDir = Join-Path $Root "dist\cache"
$EmbedZipName = "python-$PythonVersion-embed-amd64.zip"
$EmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/$EmbedZipName"
$EmbedZip = Join-Path $CacheDir $EmbedZipName
$GetPip = Join-Path $CacheDir "get-pip.py"

Write-Host "=== Self-contained engine bundle ===" -ForegroundColor Cyan
Write-Host "  Target engine: $Engine"
Write-Host "  Python embed:  $PythonVersion amd64"

# Fresh engine tree
if (Test-Path $Engine) {
    Write-Host "  Removing previous engine\ â€¦" -ForegroundColor DarkGray
    Remove-Item $Engine -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Engine | Out-Null
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

# --- 1. Download embeddable CPython (relocatable; not a dev venv) ---
if ($ForceRedownload -or -not (Test-Path $EmbedZip)) {
    Write-Host "  Downloading $EmbedUrl â€¦" -ForegroundColor Cyan
    Invoke-WebRequest -Uri $EmbedUrl -OutFile $EmbedZip -UseBasicParsing
}
if (-not (Test-Path $EmbedZip)) {
    Write-Error "Failed to download Python embeddable: $EmbedZip"
}

Write-Host "  Extracting embeddable â†’ engine\python\" -ForegroundColor Cyan
Expand-Archive -Path $EmbedZip -DestinationPath $PyHome -Force

$pythonExe = Join-Path $PyHome "python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "python.exe missing after extract: $PyHome"
}

# --- 2. Enable site-packages + pip on embeddable ---
# Default embeddable ships with import site disabled in pythonXX._pth
$pth = Get-ChildItem $PyHome -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) {
    Write-Error "python*._pth not found in embeddable layout"
}
$orig = Get-Content $pth.FullName
$zipLine = ($orig | Where-Object { $_ -match '\.zip' } | Select-Object -First 1)
if (-not $zipLine) {
    # File is python312._pth â†’ stdlib zip is python312.zip
    $zipLine = $pth.Name.Replace("._pth", ".zip")
}
$zipLine = $zipLine.Trim()
@(
    $zipLine
    "."
    "Lib\site-packages"
    "import site"
) | Set-Content -Path $pth.FullName -Encoding ASCII
Write-Host "  Enabled import site in $($pth.Name) (zip=$zipLine)" -ForegroundColor Green

if ($ForceRedownload -or -not (Test-Path $GetPip)) {
    Write-Host "  Downloading get-pip.py â€¦" -ForegroundColor Cyan
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip -UseBasicParsing
}

Write-Host "  Installing pip into embeddable â€¦" -ForegroundColor Cyan
& $pythonExe $GetPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    Write-Error "get-pip.py failed (exit $LASTEXITCODE)"
}

# --- 3. Copy app sources + install package into embeddable site-packages ---
Write-Host "  Copying src\ + project metadata â€¦" -ForegroundColor Cyan
$robocopy = @(
    @("$Root\src", "$Engine\src", "/E", "/XD", "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache"),
    @("$Root\web", "$Engine\web", "/E")
)
foreach ($rcArgs in $robocopy) {
    & robocopy @rcArgs /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Error "robocopy failed: $rcArgs" }
}
Copy-Item "$Root\pyproject.toml" $Engine -Force
if (Test-Path "$Root\LICENSE") { Copy-Item "$Root\LICENSE" $Engine -Force }

Write-Host "  pip install honestspend (+ deps) into embeddable â€¦" -ForegroundColor Cyan
# Non-editable install so the package lives fully under python\Lib\site-packages
# (relocatable; no dependency on build-machine paths)
& $pythonExe -m pip install --upgrade pip wheel setuptools --no-warn-script-location
& $pythonExe -m pip install "$Engine" --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install package failed (exit $LASTEXITCODE)"
}

# Smoke: module import without system PYTHONPATH
Write-Host "  Smoke import..." -ForegroundColor Cyan
& $pythonExe -c "import financial_os; from financial_os.cli import main; print('ok', getattr(financial_os, '__version__', '?'))"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Smoke import failed - engine is not self-contained"
}

# Marker so BackendHost.LooksLikeEngine is unambiguous
$readme = @(
    "HonestSpend self-contained engine"
    "Python: $PythonVersion embeddable amd64"
    "Users do NOT need Python installed."
    "Launch: python\python.exe -m financial_os.cli serve"
    "Built:  $(Get-Date -Format o)"
) -join "`n"
Set-Content -Path (Join-Path $Engine "README_ENGINE.txt") -Value $readme -Encoding UTF8

$launchPs1 = Join-Path $Engine "serve.ps1"
$launchBody = @(
    "# HonestSpend engine (self-contained)"
    '& "$PSScriptRoot\python\python.exe" -m financial_os.cli serve @args'
) -join "`n"
Set-Content -Path $launchPs1 -Value $launchBody -Encoding UTF8

# --- 4. Zip for MSIX / first-run extract ---
if (-not $SkipZip) {
    $dist = Join-Path $Root "dist"
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    $zipPath = Join-Path $dist "engine-portable.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Write-Host "  Zipping -> $zipPath ..." -ForegroundColor Cyan
    Compress-Archive -Path (Join-Path $Engine "*") -DestinationPath $zipPath -Force
    $zipNext = Join-Path $Target "engine-portable.zip"
    Copy-Item $zipPath $zipNext -Force
    $mb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "  engine-portable.zip: $mb MB" -ForegroundColor Green
}

Write-Host ""
Write-Host "Engine ready (self-contained)." -ForegroundColor Green
Write-Host "  $pythonExe -m financial_os.cli serve"
Write-Host "  End users: no system Python required."
Write-Host "  Store: MSIX zip extracts to %LocalAppData%\HonestSpend\engine"

