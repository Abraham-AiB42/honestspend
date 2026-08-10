# Capture 1366x768 Store screenshots from store-assets/html via Edge headless.
# Run from repo root or this folder.

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$HtmlDir = Join-Path $Here "html"
$OutDir = Join-Path $Here "screenshots"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $edge) { throw "Microsoft Edge not found" }

$shots = @(
    "01-home",
    "02-import",
    "03-books-honesty",
    "04-sort-charges",
    "05-month-close"
)

foreach ($s in $shots) {
    $html = Join-Path $HtmlDir "$s.html"
    if (-not (Test-Path $html)) { throw "Missing $html" }
    $png = Join-Path $OutDir "$s.png"
    $uri = "file:///" + ($html -replace "\\", "/")
    Write-Host "Capture $s ..."
    & $edge --headless=new --disable-gpu --hide-scrollbars --window-size=1366,768 --screenshot="$png" "$uri" | Out-Null
    if (-not (Test-Path $png)) { throw "Failed: $png" }
    Write-Host "  -> $png ($([math]::Round((Get-Item $png).Length/1KB)) KB)"
}

Write-Host "Done. Upload PNGs from $OutDir"
