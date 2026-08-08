[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ApiToken,
    [Parameter(Mandatory = $true)]
    [string]$ExtSystem,
    [string]$AuthHeader = 'Authorization',
    [string]$AuthScheme = 'Bearer',
    [ValidateRange(1, 1000)]
    [int]$MaxDetails = 100
)

$projectDirectory = Split-Path -Parent $PSCommandPath
$envPath = Join-Path $projectDirectory '.env'

$content = if (Test-Path -LiteralPath $envPath) {
    [System.Collections.Generic.List[string]](Get-Content -LiteralPath $envPath -Encoding UTF8)
} else {
    [System.Collections.Generic.List[string]]@('# Local settings. Do not commit this file.')
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $pattern = '^' + [regex]::Escape($Key) + '='
    for ($index = 0; $index -lt $content.Count; $index++) {
        if ($content[$index] -match $pattern) {
            $content[$index] = "$Key=$Value"
            return
        }
    }
    $content.Add("$Key=$Value")
}

Set-EnvValue -Key 'EAT_API_TOKEN' -Value $ApiToken
Set-EnvValue -Key 'EAT_EXT_SYSTEM' -Value $ExtSystem
Set-EnvValue -Key 'EAT_AUTH_HEADER' -Value $AuthHeader
Set-EnvValue -Key 'EAT_AUTH_SCHEME' -Value $AuthScheme
Set-EnvValue -Key 'EAT_MAX_DETAILS' -Value ([string]$MaxDetails)

Set-Content -LiteralPath $envPath -Value $content -Encoding UTF8
Write-Host ".env updated without changing other settings. Token value was not printed."

$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv (Join-Path $projectDirectory '.venv')
    & $python -m pip install -r (Join-Path $projectDirectory 'requirements.txt')
}

& $python -m tender_parser check-env --base-dir $projectDirectory
