param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$logDir = Join-Path $projectDir 'logs\supplier_intelligence'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutPath = Join-Path $logDir "sync-$runStamp.stdout.log"
$stderrPath = Join-Path $logDir "sync-$runStamp.stderr.log"
$statusPath = Join-Path $logDir 'runs.log'
$process = $null

function Write-RunStatus {
    param([string]$Message)
    Add-Content -LiteralPath $statusPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

try {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python executable not found: $PythonPath"
    }
    $env:PYTHONIOENCODING = 'utf-8'
    Write-RunStatus "START stdout=$stdoutPath stderr=$stderrPath"
    $process = Start-Process -FilePath $PythonPath `
        -ArgumentList @('-u', '-m', 'tender_parser.supplier_intelligence', 'sync') `
        -WorkingDirectory $projectDir `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden -PassThru
    $process.WaitForExit()
    $process.Refresh()
    $exitCode = [int]$process.ExitCode
    Write-RunStatus "END exit_code=$exitCode stdout=$stdoutPath stderr=$stderrPath"
    exit $exitCode
}
catch {
    Write-RunStatus "FAILED $($_.Exception.Message)"
    exit 1
}
finally {
    if ($null -ne $process) {
        $process.Dispose()
    }
}
