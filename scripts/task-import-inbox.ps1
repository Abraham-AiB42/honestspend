# HonestSpend scheduled: process bank CSV inbox (freeware money-in).
# Registered by scripts/register-tasks.ps1 as HonestSpend-ImportInbox.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "src\honestspend"))) {
  $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Set-Location $Root
& $py -m honestspend.cli import-inbox
exit $LASTEXITCODE
