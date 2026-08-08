# Launch native LedgerRing WinUI (builds if needed)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location "$Root\clients\LedgerRing.WinUI"
Write-Host "Building & running LedgerRing WinUI…" -ForegroundColor Cyan
dotnet run -c Debug -p:Platform=x64
