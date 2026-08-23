param(
    [string]$Domain = "starship-hermes.app",
    [string]$ExpectedIp = ""
)

$ErrorActionPreference = "Stop"

Write-Host "Checking DNS for $Domain..."

try {
    $aRecords = Resolve-DnsName $Domain -Type A -ErrorAction Stop
} catch {
    Write-Host "No A record found for $Domain yet."
    $aRecords = @()
}

$ips = @($aRecords | Where-Object { $_.IPAddress } | Select-Object -ExpandProperty IPAddress)
if ($ips.Count -gt 0) {
    Write-Host "A records: $($ips -join ', ')"
} else {
    Write-Host "A records: none"
}

if ($ExpectedIp) {
    if ($ips -contains $ExpectedIp) {
        Write-Host "Expected IP match: YES ($ExpectedIp)"
    } else {
        Write-Host "Expected IP match: NO (expected $ExpectedIp)"
    }
}

try {
    $wwwRecords = Resolve-DnsName "www.$Domain" -Type CNAME -ErrorAction Stop
} catch {
    Write-Host "No CNAME found for www.$Domain yet."
    $wwwRecords = @()
}

$cnames = @($wwwRecords | Where-Object { $_.NameHost } | Select-Object -ExpandProperty NameHost)
if ($cnames.Count -gt 0) {
    Write-Host "www CNAME: $($cnames -join ', ')"
} else {
    Write-Host "www CNAME: none"
}

Write-Host ""
Write-Host "Expected Cloudflare records:"
Write-Host "@     A      <GCP_STATIC_IP>"
Write-Host "www   CNAME  $Domain"
