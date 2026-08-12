# HonestSpend tray (server must already be running)
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
& .\.venv\Scripts\Activate.ps1
python -m honestspend.cli tray @args
