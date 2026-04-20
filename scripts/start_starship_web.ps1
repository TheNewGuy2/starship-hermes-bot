$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PYTHONPATH = "packages\starship_web\src;packages\starship_shared\src"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Missing virtual environment at .\.venv\Scripts\python.exe"
}

& ".\.venv\Scripts\python.exe" -m uvicorn starship_web.app:app --host 127.0.0.1 --port 8000
