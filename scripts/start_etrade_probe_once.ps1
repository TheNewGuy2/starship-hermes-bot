$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Missing virtual environment at .\.venv\Scripts\python.exe"
}

$env:PYTHONPATH = "packages\starship_engine\src;packages\starship_shared\src;packages\starship_web\src"

Write-Host "Starting live E*TRADE probe once..."
Write-Host "  ETRADE_ENV=live"
Write-Host "  Mode=once"

& ".\.venv\Scripts\python.exe" -m starship_engine.probe_cli --once --direct-slack --no-publish-web --write-jsonl
