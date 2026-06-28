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

$content = @(
    '# Local EAT/Berezka settings. Do not commit this file.',
    "EAT_API_TOKEN=$ApiToken",
    "EAT_EXT_SYSTEM=$ExtSystem",
    "EAT_AUTH_HEADER=$AuthHeader",
    "EAT_AUTH_SCHEME=$AuthScheme",
    "EAT_MAX_DETAILS=$MaxDetails"
)

Set-Content -LiteralPath $envPath -Value $content -Encoding UTF8
Write-Host ".env updated. Token value was not printed."

$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv (Join-Path $projectDirectory '.venv')
    & $python -m pip install -r (Join-Path $projectDirectory 'requirements.txt')
}

& $python -m tender_parser check-env --base-dir $projectDirectory
