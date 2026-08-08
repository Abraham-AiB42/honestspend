# LedgerRing — scheduled auto-backup (run by Task Scheduler)
# Ensures data_dir backups; does not require UI.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "src\financial_os"))) {
  $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

# Prefer auto schedule; force if FOS_BACKUP_FORCE=1
$args = @("-m", "financial_os.cli", "backup", "--auto")
if ($env:FOS_BACKUP_FORCE -eq "1") {
  $args = @("-m", "financial_os.cli", "backup", "--force", "--note", "task-scheduler", "--keep", "14")
}

& $Py @args
exit $LASTEXITCODE
