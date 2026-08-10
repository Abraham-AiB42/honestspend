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
    if ($init -notmatch '1\.0\.26') { throw "__init__.py not 1.0.26" }
    if ($pyproj -notmatch '1\.0\.26') { throw "pyproject.toml not 1.0.26" }
    $iss = Get-Content (Join-Path $Root "packaging\HonestSpend.iss") -Raw
    if ($iss -notmatch '1\.0\.26') { throw "HonestSpend.iss not 1.0.26" }
    Write-Host "version 1.0.26 consistent"
}

Step "north-star surface files" {
    $need = @(
        "src\financial_os\services\home_simple.py",
        "src\financial_os\services\wealth_basics.py",
        "docs\SIMPLE_MODE.md",
        "docs\RELEASE_0.7.0.md",
        "clients\HonestSpend.WinUI\Pages\FirstRunPage.xaml",
        "clients\HonestSpend.WinUI\Pages\AddHubPage.xaml",
        "clients\HonestSpend.WinUI\Pages\MoneyWizardPage.xaml",
        "clients\HonestSpend.WinUI\Helpers\UiCopy.cs",
        "clients\HonestSpend.WinUI\Pages\ScenariosPage.xaml",
        "scripts\_northstar_e2e.py",
        "scripts\_dogfood_e2e.py",
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

Step "dogfood e2e (RC path A)" {
    & $py (Join-Path $Root "scripts\_dogfood_e2e.py")
}

Step "1.0 release docs" {
    foreach ($f in @("docs\RELEASE_1.0.0.md", "docs\RC_1.0.md", "docs\VERSIONING.md", "docs\CLIENT_FIRST.md")) {
        if (-not (Test-Path (Join-Path $Root $f))) { throw "missing $f" }
    }
    $cf = Get-Content (Join-Path $Root "docs\CLIENT_FIRST.md") -Raw
    if ($cf -notmatch "No PWA") { throw "CLIENT_FIRST.md must ban PWA" }
    Write-Host "1.0 + client-first docs present"
}

Write-Host ""
if ($failed -gt 0) {
    Write-Host "GRADE-A VERIFY FAILED ($failed step(s))" -ForegroundColor Red
    exit 1
}
Write-Host "GRADE-A VERIFY OK - north star ship bar green" -ForegroundColor Green
exit 0
