# HonestSpend end-to-end smoke against temp data dir.
# Usage: powershell -File scripts\smoke-e2e.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== HonestSpend smoke-e2e =="
python scripts\_smoke_e2e.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "smoke-e2e passed"
