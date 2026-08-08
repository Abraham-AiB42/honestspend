# Register Windows Task Scheduler jobs for LedgerRing automation.
# Run once (no admin required for current-user tasks).
#
# Usage:
#   .\scripts\register-tasks.ps1
#   .\scripts\register-tasks.ps1 -Uninstall
#   .\scripts\register-tasks.ps1 -BackupHour 3 -DigestHour 8

param(
  [switch]$Uninstall,
  [int]$BackupHour = 3,
  [int]$DigestHour = 8
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "src\financial_os"))) {
  $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$BackupScript = Join-Path $Root "scripts\task-auto-backup.ps1"
$DigestScript = Join-Path $Root "scripts\task-digest.ps1"
$TaskBackup = "LedgerRing-AutoBackup"
$TaskDigest = "LedgerRing-Digest"

function Remove-LrTask([string]$Name) {
  $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  if ($existing) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    Write-Host "Removed task: $Name"
  }
}

if ($Uninstall) {
  Remove-LrTask $TaskBackup
  Remove-LrTask $TaskDigest
  Write-Host "Done (uninstalled)."
  exit 0
}

if (-not (Test-Path $BackupScript)) { throw "Missing $BackupScript" }
if (-not (Test-Path $DigestScript)) { throw "Missing $DigestScript" }

$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

# --- Daily backup ---
Remove-LrTask $TaskBackup
$actionB = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`""
$triggerB = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($BackupHour))
$settingsB = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principalB = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskBackup -Action $actionB -Trigger $triggerB -Settings $settingsB -Principal $principalB `
  -Description "LedgerRing local SQLite auto-backup (respects schedule / force via env)" | Out-Null
Write-Host "Registered: $TaskBackup (daily ${BackupHour}:00)"

# --- Daily digest ---
Remove-LrTask $TaskDigest
$actionD = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$DigestScript`""
$triggerD = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($DigestHour))
$settingsD = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principalD = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskDigest -Action $actionD -Trigger $triggerD -Settings $settingsD -Principal $principalD `
  -Description "LedgerRing daily digest (exit 2 if critical); may start engine if offline" | Out-Null
Write-Host "Registered: $TaskDigest (daily ${DigestHour}:00)"

Write-Host ""
Write-Host "Manual run:"
Write-Host "  schtasks /Run /TN $TaskBackup"
Write-Host "  schtasks /Run /TN $TaskDigest"
Write-Host "Uninstall: .\scripts\register-tasks.ps1 -Uninstall"
Write-Host "Logon tray-only: enable in WinUI Settings (HKCU Run --tray-only)"
