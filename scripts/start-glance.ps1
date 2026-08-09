# Windows: open multi-platform Glance UI (engine auto-started if needed).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$hostName = if ($env:FOS_HOST) { $env:FOS_HOST } else { "127.0.0.1" }
$port = if ($env:FOS_PORT) { [int]$env:FOS_PORT } else { 7420 }
$base = "http://${hostName}:${port}"

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "$base/api/health" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch { return $false }
}

if (-not (Test-Health)) {
    Write-Host "Starting engine…"
    if (Test-Path ".\.venv\Scripts\python.exe") {
        $py = ".\.venv\Scripts\python.exe"
    } else {
        $py = "python"
    }
    Start-Process -FilePath $py -ArgumentList "-m","financial_os.cli","serve","--host",$hostName,"--port","$port" -WindowStyle Minimized
    for ($i = 0; $i -lt 40; $i++) {
        if (Test-Health) { break }
        Start-Sleep -Milliseconds 500
    }
}

Write-Host "Glance → $base/glance"
Start-Process "$base/glance"
