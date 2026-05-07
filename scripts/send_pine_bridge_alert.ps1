param(
    [ValidateSet("ENTRY", "MANAGE", "CLOSE")]
    [string]$EventType = "ENTRY",

    [string]$Url = "http://127.0.0.1:8000/webhooks/tradingview",
    [string]$Secret = "",
    [string]$EventId = "",
    [string]$StrategyName = "hermes_v3_2",
    [string]$PositionKey = "spx-main",
    [string]$Underlier = "SPX",
    [string]$OptionRoot = "SPXW",
    [string]$BarTime = "",
    [int]$ExpiryYear = 0,
    [int]$ExpiryMonth = 0,
    [int]$ExpiryDay = 0,

    [int]$Quantity = 1,
    [double]$ShortPut = 7105,
    [double]$LongPut = 7085,
    [double]$ShortCall = 7115,
    [double]$LongCall = 7135,
    [double]$TargetEntryCredit = 1.45,
    [double]$TargetCloseDebit = 0.65,

    [double]$Vix1d = 18.2,
    [double]$Vix = 19.1,
    [double]$Vix9d = 17.8,
    [double]$Vvix = 86.0,
    [double]$Skew = 142.5,
    [double]$Add = 950,
    [double]$ZTrend = 0.65,
    [double]$ZShortRisk = 1.18,

    [switch]$ShowPayload,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $BarTime) {
    $BarTime = (Get-Date).ToString("o")
}

if (-not $EventId) {
    $EventId = "tvb-$($EventType.ToLowerInvariant())-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
}

$hasAnyExpiry = ($ExpiryYear -gt 0) -or ($ExpiryMonth -gt 0) -or ($ExpiryDay -gt 0)
if ($hasAnyExpiry) {
    if (($ExpiryYear -le 0) -or ($ExpiryMonth -le 0) -or ($ExpiryDay -le 0)) {
        throw "If you pass any expiry override, you must pass -ExpiryYear, -ExpiryMonth, and -ExpiryDay together."
    }
}

$payload = [ordered]@{
    strategy_name = $StrategyName
    event_type = $EventType
    event_id = $EventId
    position_key = $PositionKey
    underlier = $Underlier
    option_root = $OptionRoot
    bar_time = $BarTime
    quantity = $Quantity
    vix1d = $Vix1d
    vix = $Vix
    vix9d = $Vix9d
    vvix = $Vvix
    skew = $Skew
    add = $Add
    zTrend = $ZTrend
    zShortRisk = $ZShortRisk
}

if ($hasAnyExpiry) {
    $payload.expiry_year = $ExpiryYear
    $payload.expiry_month = $ExpiryMonth
    $payload.expiry_day = $ExpiryDay
}

if ($Secret) {
    $payload.secret = $Secret
}

switch ($EventType) {
    "ENTRY" {
        $payload.short_put = $ShortPut
        $payload.long_put = $LongPut
        $payload.short_call = $ShortCall
        $payload.long_call = $LongCall
        $payload.target_entry_credit = $TargetEntryCredit
    }
    "CLOSE" {
        $payload.target_close_debit = $TargetCloseDebit
    }
}

$json = $payload | ConvertTo-Json -Depth 6

if ($ShowPayload -or $DryRun) {
    Write-Host ""
    Write-Host "Payload:"
    Write-Host $json
    Write-Host ""
}

if ($DryRun) {
    Write-Host "Dry run only. No request was sent."
    exit 0
}

Write-Host "POST $Url"
Write-Host "  strategy_name=$StrategyName"
Write-Host "  event_type=$EventType"
Write-Host "  event_id=$EventId"
Write-Host "  position_key=$PositionKey"
if ($hasAnyExpiry) {
    Write-Host "  expiry=$ExpiryYear-$ExpiryMonth-$ExpiryDay"
}

$response = Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json" -Body $json

Write-Host ""
Write-Host "Response:"
$response | ConvertTo-Json -Depth 8
