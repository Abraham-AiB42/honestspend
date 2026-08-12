# HonestSpend — scheduled digest (exit 2 if critical — for monitoring)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "src\honestspend"))) {
  $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

# Optional: wake engine if offline (best-effort, non-blocking)
$Health = & $Py -m honestspend.cli health 2>$null
if ($LASTEXITCODE -ne 0) {
  # Start serve in background if not up
  $LogDir = Join-Path $env:USERPROFILE ".HonestSpend"
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  $Log = Join-Path $LogDir "task-serve.log"
  Start-Process -FilePath $Py -ArgumentList "-m","honestspend.cli","serve","--host","127.0.0.1","--port","7420" `
    -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $Log -RedirectStandardError $Log
  Start-Sleep -Seconds 4
}

& $Py -m honestspend.cli digest
$Code = $LASTEXITCODE
# Write last digest for tray/UI tools
$Out = Join-Path $env:USERPROFILE ".HonestSpend\last_digest.json"
try {
  & $Py -m honestspend.cli digest 2>$null | Out-File -FilePath $Out -Encoding utf8
} catch {}
exit $Code
