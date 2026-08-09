# Grade-A ship bar: pytest + northstar e2e + smoke + name-gate + package layout checks.
# Usage (repo root):
#   .\scripts\verify-grade-a.ps1
#   .\scripts\verify-grade-a.ps1 -SkipPackageLayout

param(
    [switch]$SkipPackageLayout
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$failed = 0
function Step([string]$name, [scriptblock]$block) {
    Write-Host ""
    Write-Host "== $name ==" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    try {
        & $block
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        if ($code -ne 0) {
            Write-Host "FAIL $name (exit $code)" -ForegroundColor Red
            $script:failed++
        } else {
            Write-Host "OK $name" -ForegroundColor Green
        }
    } catch {
        Write-Host "FAIL $name : $_" -ForegroundColor Red
        $script:failed++
    }
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Step "pytest" {
    & $py -m pytest -q --tb=line
}

Step "northstar e2e" {
    & $py (Join-Path $Root "scripts\_northstar_e2e.py")
}

Step "smoke e2e" {
    & $py (Join-Path $Root "scripts\_smoke_e2e.py")
}

Step "private-name gate" {
    & powershell -NoProfile -File (Join-Path $Root "scripts\check-no-private-names.ps1")
}

Step "version sync" {
    $init = Get-Content (Join-Path $Root "src\financial_os\__init__.py") -Raw
    $pyproj = Get-Content (Join-Path $Root "pyproject.toml") -Raw
    if ($init -notmatch '0\.8\.0') { throw "__init__.py not 0.8.0" }
    if ($pyproj -notmatch '0\.8\.0') { throw "pyproject.toml not 0.8.0" }
    $iss = Get-Content (Join-Path $Root "packaging\LedgerRing.iss") -Raw
    if ($iss -notmatch '0\.8\.0') { throw "LedgerRing.iss not 0.8.0" }
    Write-Host "version 0.8.0 consistent"
}

Step "north-star surface files" {
    $need = @(
        "src\financial_os\services\home_simple.py",
        "src\financial_os\services\wealth_basics.py",
        "docs\SIMPLE_MODE.md",
        "docs\RELEASE_0.7.0.md",
        "clients\LedgerRing.WinUI\Pages\FirstRunPage.xaml",
        "clients\LedgerRing.WinUI\Pages\AddHubPage.xaml",
        "clients\LedgerRing.WinUI\Pages\MoneyWizardPage.xaml",
        "clients\LedgerRing.WinUI\Helpers\UiCopy.cs",
        "scripts\_northstar_e2e.py",
        "scripts\package-release.ps1",
        "scripts\prepare-engine-bundle.ps1"
    )
    foreach ($f in $need) {
        $p = Join-Path $Root $f
        if (-not (Test-Path $p)) { throw "missing $f" }
    }
    Write-Host "surface files present"
}

if (-not $SkipPackageLayout) {
    Step "package scripts present" {
        $scripts = @("publish-winui.ps1", "prepare-engine-bundle.ps1", "package-release.ps1", "start-winui.ps1")
        foreach ($s in $scripts) {
            if (-not (Test-Path (Join-Path $Root "scripts\$s"))) { throw "missing scripts\$s" }
        }
        # Install docs describe one-command package
        $inst = Get-Content (Join-Path $Root "docs\INSTALL.md") -Raw
        if ($inst -notmatch "package-release") { throw "INSTALL.md missing package-release" }
        if ($inst -notmatch "engine\\") { throw "INSTALL.md missing engine layout" }
        Write-Host "install + package path documented"
    }
}

Write-Host ""
if ($failed -gt 0) {
    Write-Host "GRADE-A VERIFY FAILED ($failed step(s))" -ForegroundColor Red
    exit 1
}
Write-Host "GRADE-A VERIFY OK - north star ship bar green" -ForegroundColor Green
exit 0
