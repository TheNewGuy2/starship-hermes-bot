$ErrorActionPreference = "Stop"

param(
    [int]$PollSeconds = 60,
    [int]$CooldownSeconds = 300
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Missing virtual environment at .\.venv\Scripts\python.exe"
}

$env:PYTHONPATH = "packages\starship_engine\src;packages\starship_shared\src;packages\starship_web\src"

Write-Host "Starting live E*TRADE probe loop..."
Write-Host "  ETRADE_ENV=live"
Write-Host "  PollSeconds=$PollSeconds"
Write-Host "  CooldownSeconds=$CooldownSeconds"

& ".\.venv\Scripts\python.exe" -m starship_engine.probe_cli --loop --poll-seconds $PollSeconds --cooldown-seconds $CooldownSeconds --direct-slack --no-publish-web --write-jsonl
