[CmdletBinding()]
param(
    [ValidateRange(5, 180)]
    [int]$TimeoutMinutes = 45
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$logsDirectory = Join-Path $projectDirectory 'logs'
$logPath = Join-Path $logsDirectory 'rts_background.log'

New-Item -ItemType Directory -Force -Path $logsDirectory | Out-Null

function Write-RtsLog {
    param([string]$Message)
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-RtsLog "Python environment not found: $pythonPath"
    exit 2
}

$mutex = [System.Threading.Mutex]::new($false, 'Local\TenderParserRtsBackgroundGuard')
$hasMutex = $false
$process = $null
try {
    $hasMutex = $mutex.WaitOne(0)
    if (-not $hasMutex) {
        Write-RtsLog 'Another isolated RTS refresh is already running; trigger skipped.'
        exit 0
    }

    $stdoutPath = Join-Path $logsDirectory 'rts_background.stdout.log'
    $stderrPath = Join-Path $logsDirectory 'rts_background.stderr.log'
    Write-RtsLog "Starting isolated RTS refresh. Timeout=$TimeoutMinutes min."
    $processArguments = "-m tender_parser rts-refresh --base-dir `"$projectDirectory`""
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList $processArguments `
        -WorkingDirectory $projectDirectory `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    $completed = $process.WaitForExit($TimeoutMinutes * 60 * 1000)
    if (-not $completed) {
        Write-RtsLog 'Hard timeout reached; terminating only the isolated RTS process.'
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        exit 124
    }

    # With redirected output Windows PowerShell can leave ExitCode unset after
    # the timed overload. The parameterless wait flushes async stream handlers.
    $process.WaitForExit()
    $process.Refresh()
    $parserExitCode = $process.ExitCode
    if ($null -eq $parserExitCode) {
        $statePath = Join-Path (Join-Path $projectDirectory 'data') 'rts_background_state.json'
        if (Test-Path -LiteralPath $statePath) {
            try {
                $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
                if ($null -ne $state.exit_code) {
                    $parserExitCode = [int]$state.exit_code
                }
            }
            catch {
                Write-RtsLog "Cannot read RTS state exit code: $($_.Exception.Message)"
            }
        }
    }
    if ($null -eq $parserExitCode) {
        Write-RtsLog 'RTS refresh exit code is unavailable; treating the run as failed.'
        exit 2
    }
    Write-RtsLog "RTS refresh finished with exit code $parserExitCode."
    exit $parserExitCode
}
catch {
    Write-RtsLog "RTS runner failed: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
    if ($null -ne $process) {
        $process.Dispose()
    }
}
