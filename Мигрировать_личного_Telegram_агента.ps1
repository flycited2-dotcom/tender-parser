[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$LegacyProjectPath
)

$ErrorActionPreference = 'Stop'
$targetProjectPath = Split-Path -Parent $PSCommandPath
$sourceProjectPath = (Resolve-Path -LiteralPath $LegacyProjectPath).Path
$sourceEnvPath = Join-Path $sourceProjectPath '.env'
$targetEnvPath = Join-Path $targetProjectPath '.env'

if (-not (Test-Path -LiteralPath $sourceEnvPath)) {
    throw "Legacy environment file not found: $sourceEnvPath"
}
if (-not (Test-Path -LiteralPath $targetEnvPath)) {
    throw "Target environment file not found: $targetEnvPath"
}

function Read-EnvValues([string]$Path) {
    $result = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $parts = $line -split '=', 2
        $result[$parts[0].Trim()] = $parts[1]
    }
    return $result
}

$sourceValues = Read-EnvValues $sourceEnvPath
$targetValues = Read-EnvValues $targetEnvPath
$mapping = [ordered]@{
    TELEGRAM_AGENT_BOT_TOKEN        = 'TELEGRAM_BOT_TOKEN'
    TELEGRAM_AGENT_ALLOWED_USER_IDS = 'TELEGRAM_ALLOWED_USER_IDS'
    TENDER_SUPPLIER_API_URL         = 'TENDER_SUPPLIER_API_URL'
    TENDER_SUPPLIER_API_TOKEN       = 'TENDER_SUPPLIER_API_TOKEN'
    TELEGRAM_CODEX_SESSION_ID       = 'TELEGRAM_CODEX_SESSION_ID'
}

$updates = [ordered]@{}
foreach ($entry in $mapping.GetEnumerator()) {
    $targetName = $entry.Key
    $sourceName = $entry.Value
    if ($targetValues.ContainsKey($targetName) -and $targetValues[$targetName].Trim()) {
        continue
    }
    if ($sourceValues.ContainsKey($sourceName) -and $sourceValues[$sourceName].Trim()) {
        $updates[$targetName] = $sourceValues[$sourceName]
    }
}

if ($updates.Count -eq 0) {
    Write-Host 'No environment values require migration.'
} elseif ($PSCmdlet.ShouldProcess($targetEnvPath, 'Migrate personal agent settings')) {
    $backupPath = "$targetEnvPath.before-agent-migration-$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
    Copy-Item -LiteralPath $targetEnvPath -Destination $backupPath
    $lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $targetEnvPath -Encoding UTF8)
    foreach ($entry in $updates.GetEnumerator()) {
        $replaced = $false
        for ($index = 0; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match ('^' + [regex]::Escape($entry.Key) + '=')) {
                $lines[$index] = "$($entry.Key)=$($entry.Value)"
                $replaced = $true
                break
            }
        }
        if (-not $replaced) { $lines.Add("$($entry.Key)=$($entry.Value)") }
    }
    [System.IO.File]::WriteAllLines($targetEnvPath, $lines, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Migrated settings: $($updates.Keys -join ', ')"
    Write-Host "Backup created: $backupPath"
}

foreach ($stateName in @('telegram_codex_session.json', 'telegram_agent_state.json')) {
    $sourceState = Join-Path $sourceProjectPath "data\$stateName"
    $targetState = Join-Path $targetProjectPath "data\$stateName"
    if ((Test-Path -LiteralPath $sourceState) -and -not (Test-Path -LiteralPath $targetState)) {
        if ($PSCmdlet.ShouldProcess($targetState, 'Copy personal agent state')) {
            Copy-Item -LiteralPath $sourceState -Destination $targetState
            Write-Host "Copied state: $stateName"
        }
    }
}
