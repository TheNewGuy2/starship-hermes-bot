$ErrorActionPreference = "Stop"

param(
    [switch]$Live
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Missing virtual environment at .\.venv\Scripts\python.exe"
}

$env:PYTHONPATH = "packages\starship_engine\src;packages\starship_shared\src;packages\starship_web\src"
$env:ETRADE_RUN_ONCE = "true"
$env:ETRADE_POLL_SECONDS = "15"
$env:ETRADE_SIGNAL_COOLDOWN_SECONDS = "30"

if ($Live) {
    $env:ETRADE_ENV = "live"
}

Write-Host "Starting E*TRADE probe once..."
Write-Host "  BROKER_PROVIDER=$($env:BROKER_PROVIDER)"
Write-Host "  ETRADE_ENV=$($env:ETRADE_ENV)"
Write-Host "  ETRADE_RUN_ONCE=$($env:ETRADE_RUN_ONCE)"

& ".\.venv\Scripts\python.exe" -m starship_engine.runner
