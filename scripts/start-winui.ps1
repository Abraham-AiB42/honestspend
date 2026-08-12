# Launch native HonestSpend WinUI (builds if needed)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location "$Root\clients\HonestSpend.WinUI"
Write-Host "Building & running HonestSpend WinUI (unpackaged)..." -ForegroundColor Cyan
Write-Host "If a window does not appear, check %USERPROFILE%\.HonestSpend\winui-crash.log" -ForegroundColor DarkGray
dotnet run -c Debug -p:Platform=x64 -p:WindowsPackageType=None --launch-profile "HonestSpend.WinUI (Unpackaged)"
