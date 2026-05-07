param(
    [string]$Url = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exe = ".\\tools\\cloudflared\\cloudflared.exe"

if (-not (Test-Path $exe)) {
    throw "Missing cloudflared binary at $exe"
}

Write-Host "Starting cloudflared tunnel..."
Write-Host "  URL=$Url"
Write-Host "  Binary=$exe"

& $exe tunnel --edge-ip-version 4 --url $Url
