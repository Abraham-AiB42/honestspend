# Fail if private/legacy entity names reappear in product sources.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$patterns = @(
    "ap_agency",
    "aib42",
    "AiB42",
    "AP Agency"
)

$include = @("*.py", "*.cs", "*.xaml", "*.md", "*.html", "*.js", "*.json", "*.ps1", "*.toml")
$excludeDirs = @(".venv", "bin", "obj", ".git", "__pycache__", "node_modules", "dist")
$self = $MyInvocation.MyCommand.Path

$hits = @()
Get-ChildItem -Recurse -File -Include $include | Where-Object {
    $p = $_.FullName
    if ($self -and ($p -eq $self)) { return $false }
    if ($_.Name -eq "check-no-private-names.ps1") { return $false }
    if ($_.Name -eq "smoke-e2e.ps1") { return $false }
    if ($_.Name -eq "_smoke_e2e.py") { return $false }
    if ($_.Name -eq "_northstar_e2e.py") { return $false }
    foreach ($d in $excludeDirs) {
        if ($p -match [regex]::Escape([IO.Path]::DirectorySeparatorChar + $d + [IO.Path]::DirectorySeparatorChar)) {
            return $false
        }
    }
    $true
} | ForEach-Object {
    $text = Get-Content -Raw -LiteralPath $_.FullName -ErrorAction SilentlyContinue
    if (-not $text) { return }
    foreach ($pat in $patterns) {
        if ($text -match [regex]::Escape($pat)) {
            $hits += "$($_.FullName): $pat"
        }
    }
}

if ($hits.Count -gt 0) {
    Write-Host "Private name gate FAILED:"
    $hits | Select-Object -First 50 | ForEach-Object { Write-Host "  $_" }
    exit 1
}

Write-Host "Private name gate OK (no AP Agency / AiB42 / ap_agency / aib42)."
exit 0
