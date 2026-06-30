param(
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

$happRoot = "C:\Program Files\FlyFrogLLC\Happ"
$configPath = Join-Path $env:LOCALAPPDATA "Happ\config.json"
$routingPath = Join-Path $env:LOCALAPPDATA "Happ\routing.json"
$singBoxPath = Join-Path $happRoot "tun\sing-box.exe"
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Happ config not found: $configPath"
}
if (-not (Test-Path -LiteralPath $routingPath)) {
    throw "Happ routing profile not found: $routingPath"
}
if (-not (Test-Path -LiteralPath $singBoxPath)) {
    throw "sing-box not found: $singBoxPath"
}
if (-not (Test-Path -LiteralPath $chromePath)) {
    throw "Chrome not found: $chromePath"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item -LiteralPath $configPath -Destination "$configPath.bak-rts-$stamp"
Copy-Item -LiteralPath $routingPath -Destination "$routingPath.bak-rts-$stamp"

$rtsSites = @(
    "domain:rts-tender.ru",
    "domain:www.rts-tender.ru",
    "domain:223.rts-tender.ru",
    "domain:market.rts-tender.ru",
    "domain:zakupki-simferopol.rts-tender.ru",
    "domain:yalta-zmo.rts-tender.ru"
)

$routing = Get-Content -Raw -LiteralPath $routingPath | ConvertFrom-Json
$activeName = $routing.activeRoutingName
$profile = $routing.routings | Where-Object { $_.name -eq $activeName } | Select-Object -First 1
if (-not $profile) {
    throw "Active Happ routing profile not found: $activeName"
}

$directSites = @($profile.directSites)
foreach ($site in $rtsSites) {
    if ($directSites -notcontains $site) {
        $directSites += $site
    }
}
$profile.directSites = @($directSites)
$profile.lastUpdated = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$pathRule = $config.route.rules |
    Where-Object { $_.outbound -eq "direct" -and ($_.PSObject.Properties.Name -contains "process_path") } |
    Select-Object -First 1
if (-not $pathRule) {
    $pathRule = [pscustomobject]([ordered]@{
        outbound = "direct"
        process_path = @()
    })
    $config.route.rules = @($pathRule) + @($config.route.rules)
}

$processPaths = @($pathRule.process_path)
if ($processPaths -notcontains $chromePath) {
    $processPaths += $chromePath
}
$pathRule.process_path = @($processPaths)

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($routingPath, ($routing | ConvertTo-Json -Depth 30), $utf8NoBom)
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 30), $utf8NoBom)

& $singBoxPath check -c $configPath
if ($LASTEXITCODE -ne 0) {
    throw "sing-box config check failed with exit code $LASTEXITCODE"
}

if (-not $NoRestart) {
    Get-Process -Name "sing-box" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$happRoot*" } |
        Stop-Process -Force
    Start-Sleep -Seconds 6
}

$isRunning = [bool](Get-Process -Name "sing-box" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "$happRoot*" } |
    Select-Object -First 1)

Write-Output "Happ RTS direct routing configured."
Write-Output "Backups:"
Write-Output "  $configPath.bak-rts-$stamp"
Write-Output "  $routingPath.bak-rts-$stamp"
Write-Output "Chrome direct path:"
Write-Output "  $chromePath"
Write-Output "sing-box running: $isRunning"
