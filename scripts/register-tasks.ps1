# Register Windows Task Scheduler jobs for HonestSpend automation.
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
$InboxScript = Join-Path $Root "scripts\task-import-inbox.ps1"
$TaskBackup = "HonestSpend-AutoBackup"
$TaskDigest = "HonestSpend-Digest"
$TaskInbox = "HonestSpend-ImportInbox"

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
  Remove-LrTask $TaskInbox
  # legacy names (Floatpile / LedgerRing era)
  Remove-LrTask "Floatpile-AutoBackup"
  Remove-LrTask "Floatpile-Digest"
  Remove-LrTask "Floatpile-ImportInbox"
  Remove-LrTask "LedgerRing-AutoBackup"
  Remove-LrTask "LedgerRing-Digest"
  Write-Host "Done (uninstalled)."
  exit 0
}

# Drop prior brand task names when re-registering under HonestSpend
Remove-LrTask "Floatpile-AutoBackup"
Remove-LrTask "Floatpile-Digest"
Remove-LrTask "Floatpile-ImportInbox"
Remove-LrTask "LedgerRing-AutoBackup"
Remove-LrTask "LedgerRing-Digest"

if (-not (Test-Path $BackupScript)) { throw "Missing $BackupScript" }
if (-not (Test-Path $DigestScript)) { throw "Missing $DigestScript" }
if (-not (Test-Path $InboxScript)) { throw "Missing $InboxScript" }

$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

# --- Daily backup ---
Remove-LrTask $TaskBackup
$actionB = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`""
$triggerB = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($BackupHour))
$settingsB = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principalB = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskBackup -Action $actionB -Trigger $triggerB -Settings $settingsB -Principal $principalB `
  -Description "HonestSpend local SQLite auto-backup (respects schedule / force via env)" | Out-Null
Write-Host "Registered: $TaskBackup (daily ${BackupHour}:00)"

# --- Daily digest ---
Remove-LrTask $TaskDigest
$actionD = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$DigestScript`""
$triggerD = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($DigestHour))
$settingsD = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principalD = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskDigest -Action $actionD -Trigger $triggerD -Settings $settingsD -Principal $principalD `
  -Description "HonestSpend daily digest (exit 2 if critical); may start engine if offline" | Out-Null
Write-Host "Registered: $TaskDigest (daily ${DigestHour}:00)"

# --- Daily inbox import (bank CSV drop folder) ---
Remove-LrTask $TaskInbox
$actionI = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InboxScript`""
$triggerI = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(9))
$settingsI = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principalI = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskInbox -Action $actionI -Trigger $triggerI -Settings $settingsI -Principal $principalI `
  -Description "HonestSpend import bank CSVs from data_dir/inbox (freeware drop folder)" | Out-Null
Write-Host "Registered: $TaskInbox (daily 09:00)"

Write-Host ""
Write-Host "Manual run:"
Write-Host "  schtasks /Run /TN $TaskBackup"
Write-Host "  schtasks /Run /TN $TaskDigest"
Write-Host "  schtasks /Run /TN $TaskInbox"
Write-Host "Uninstall: .\scripts\register-tasks.ps1 -Uninstall"
Write-Host "Logon tray-only: enable in WinUI Settings (HKCU Run --tray-only)"
