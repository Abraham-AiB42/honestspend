# Smoke checklist for logon / tray-only automation (no interactive UI required).
# Usage: .\scripts\smoke-logon.ps1

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$Fail = 0
function Ok($m) { Write-Host "[ok] $m" -ForegroundColor Green }
function Bad($m) { Write-Host "[!!] $m" -ForegroundColor Red; $script:Fail++ }

Write-Host "=== Floatpile logon / automation smoke ===" -ForegroundColor Cyan
Write-Host "Root: $Root"

# 1. version / health CLI
& $Py -m financial_os.cli version
if ($LASTEXITCODE -eq 0) { Ok "version" } else { Bad "version" }

# 2. backup --force
$bak = & $Py -m financial_os.cli backup --force --note smoke 2>&1 | Out-String
if ($bak -match '"ok": true' -or $bak -match '"ok":true') { Ok "backup --force" } else { Bad "backup: $bak" }

# 3. digest
& $Py -m financial_os.cli digest | Out-Null
# exit 0 or 2 both ok (2 = critical alerts)
if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 2) { Ok "digest (exit $LASTEXITCODE)" } else { Bad "digest exit $LASTEXITCODE" }

# 4. health (engine may be down — not fatal)
& $Py -m financial_os.cli health | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "engine healthy" } else { Write-Host "[--] engine offline (start WinUI or serve)" -ForegroundColor Yellow }

# 5. scheduled tasks registered?
foreach ($t in @("Floatpile-AutoBackup", "Floatpile-Digest")) {
  $st = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
  if ($st) { Ok "task $t ($($st.State))" } else { Write-Host "[--] task $t not registered (run register-tasks.ps1)" -ForegroundColor Yellow }
}

# 6. logon Run key
$run = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Floatpile" -ErrorAction SilentlyContinue
if ($run) {
  Ok "HKCU Run Floatpile = $($run.Floatpile)"
  if ($run.Floatpile -match "tray-only") { Ok "logon uses --tray-only" }
  else { Write-Host "[--] logon command missing --tray-only (enable in Settings)" -ForegroundColor Yellow }
} else {
  Write-Host "[--] logon launch not enabled (WinUI Settings)" -ForegroundColor Yellow
}

# 7. tray pid file
$pidFile = Join-Path $env:USERPROFILE ".financial-os\tray.pid"
if (Test-Path $pidFile) { Ok "tray.pid exists ($((Get-Content $pidFile -Raw).Trim()))" }
else { Write-Host "[--] tray not running" -ForegroundColor Yellow }

Write-Host ""
if ($Fail -eq 0) {
  Write-Host "Smoke finished with no hard failures." -ForegroundColor Green
  exit 0
} else {
  Write-Host "Smoke finished with $Fail hard failure(s)." -ForegroundColor Red
  exit 1
}
