[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectDirectory 'logs'
$logPath = Join-Path $logDirectory 'outreach_watchdog.log'

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Python not found"
    exit 2
}

Push-Location $projectDirectory
try {
    & $pythonPath -m tender_parser.outreach_watchdog *>> $logPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
