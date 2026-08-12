# HonestSpend — Windows launcher
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]" -q
Write-Host "HonestSpend → http://127.0.0.1:7420" -ForegroundColor Cyan
python -m honestspend.cli serve @args
