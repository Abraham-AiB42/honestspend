# Launch native Floatpile WinUI (builds if needed)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location "$Root\clients\Floatpile.WinUI"
Write-Host "Building & running Floatpile WinUI…" -ForegroundColor Cyan
dotnet run -c Debug -p:Platform=x64
