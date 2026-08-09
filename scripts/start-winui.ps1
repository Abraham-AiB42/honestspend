# Launch native HonestSpend WinUI (builds if needed)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location "$Root\clients\HonestSpend.WinUI"
Write-Host "Building & running HonestSpend WinUI…" -ForegroundColor Cyan
dotnet run -c Debug -p:Platform=x64
