[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$CredentialsFile,
    [string]$SpreadsheetId = '1YEIorEc0pAHgfABVLL3OS9CoU_Ee-e8eHY9np6E_6n0',
    [string]$SpreadsheetUrl = 'https://docs.google.com/spreadsheets/d/1YEIorEc0pAHgfABVLL3OS9CoU_Ee-e8eHY9np6E_6n0/edit'
)

$projectDirectory = Split-Path -Parent $PSCommandPath
$sourcePath = (Resolve-Path -LiteralPath $CredentialsFile).Path
try {
    $credentials = Get-Content -LiteralPath $sourcePath -Raw | ConvertFrom-Json
}
catch {
    throw "Cannot read Google credentials JSON: $($_.Exception.Message)"
}
if ($credentials.type -ne 'service_account' -or -not $credentials.client_email) {
    throw 'The JSON file is not a Google service-account key.'
}

$secretsDirectory = Join-Path $projectDirectory 'secrets'
$targetPath = Join-Path $secretsDirectory 'google-service-account.json'
$localEnvPath = Join-Path $projectDirectory '.env.local'

function Set-EnvValue {
    param([string]$Path, [string]$Key, [string]$Value)
    $lines = if (Test-Path -LiteralPath $Path) { Get-Content -LiteralPath $Path } else { @() }
    $pattern = '^' + [regex]::Escape($Key) + '='
    $updated = $false
    $result = foreach ($line in $lines) {
        if ($line -match $pattern) {
            "$Key=$Value"
            $updated = $true
        }
        else {
            $line
        }
    }
    if (-not $updated) {
        $result += "$Key=$Value"
    }
    Set-Content -LiteralPath $Path -Value $result -Encoding UTF8
}

if ($PSCmdlet.ShouldProcess($targetPath, 'Install Google service-account credentials')) {
    New-Item -ItemType Directory -Force -Path $secretsDirectory | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
    Set-EnvValue -Path $localEnvPath -Key 'GOOGLE_SHEETS_ENABLED' -Value '1'
    Set-EnvValue -Path $localEnvPath -Key 'GOOGLE_SHEETS_SPREADSHEET_ID' -Value $SpreadsheetId
    Set-EnvValue -Path $localEnvPath -Key 'GOOGLE_SHEETS_URL' -Value $SpreadsheetUrl
    Set-EnvValue -Path $localEnvPath -Key 'GOOGLE_SERVICE_ACCOUNT_FILE' -Value 'secrets/google-service-account.json'
    Write-Host "Credentials installed. Share the Google Sheet with Editor access for: $($credentials.client_email)"
}
