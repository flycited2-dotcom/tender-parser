[CmdletBinding()]
param(
    [ValidateSet('full', 'fast', 'local', 'rts')]
    [string]$Profile = 'fast',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$ScheduleTime = '08:00',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$launcherPath = Join-Path $projectDirectory 'run_tender_parser_silent.bat'
$dataDirectory = Join-Path $projectDirectory 'data'
$exportsDirectory = Join-Path $projectDirectory 'exports'
$logsDirectory = Join-Path $projectDirectory 'logs'
$statePath = Join-Path $dataDirectory 'scheduler_state.json'
$schedulerLogPath = Join-Path $logsDirectory 'scheduler.log'

New-Item -ItemType Directory -Force -Path $dataDirectory, $logsDirectory | Out-Null

function Write-SchedulerLog {
    param([string]$Message)
    Add-Content -LiteralPath $schedulerLogPath -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

if (-not (Test-Path -LiteralPath $launcherPath)) {
    Write-SchedulerLog "Launcher not found: $launcherPath"
    exit 2
}

$mutex = [System.Threading.Mutex]::new($false, 'Local\TenderParserDailyGuard')
$hasMutex = $false
try {
    $hasMutex = $mutex.WaitOne(0)
    if (-not $hasMutex) {
        Write-SchedulerLog 'Another parser cycle is already running; duplicate trigger skipped.'
        exit 0
    }

    $now = Get-Date
    $scheduledSpan = [TimeSpan]::ParseExact($ScheduleTime, 'hh\:mm', $null)
    $dueBoundary = $now.Date.Add($scheduledSpan)
    if ($now -lt $dueBoundary) {
        $dueBoundary = $dueBoundary.AddDays(-1)
    }

    $lastSuccess = $null
    $usedRunReportFallback = $false
    if (Test-Path -LiteralPath $statePath) {
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            if ($state.last_success_at) {
                $lastSuccess = [datetime]::Parse($state.last_success_at, $null, [Globalization.DateTimeStyles]::RoundtripKind)
            }
        }
        catch {
            Write-SchedulerLog "State file is unreadable; falling back to the latest run report: $($_.Exception.Message)"
        }
    }

    if ($null -eq $lastSuccess) {
        $runReportPath = Join-Path $exportsDirectory 'run_report.json'
        if (Test-Path -LiteralPath $runReportPath) {
            $lastSuccess = (Get-Item -LiteralPath $runReportPath).LastWriteTime
            $usedRunReportFallback = $true
        }
    }

    if (-not $Force -and $null -ne $lastSuccess -and $lastSuccess -ge $dueBoundary) {
        if ($usedRunReportFallback) {
            @{
                last_success_at = $lastSuccess.ToString('o')
                profile = $Profile
            } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
        }
        Write-SchedulerLog "Successful cycle already exists ($($lastSuccess.ToString('s'))); trigger skipped."
        exit 0
    }

    Write-SchedulerLog "Starting catch-up cycle. Profile=$Profile; due=$($dueBoundary.ToString('s'))."
    $previousProfile = $env:TENDER_PARSER_PROFILE
    $env:TENDER_PARSER_PROFILE = $Profile
    try {
        & $launcherPath
        $parserExitCode = $LASTEXITCODE
    }
    finally {
        $env:TENDER_PARSER_PROFILE = $previousProfile
    }

    if ($parserExitCode -ne 0) {
        Write-SchedulerLog "Parser failed with exit code $parserExitCode; Task Scheduler will retry."
        exit $parserExitCode
    }

    @{
        last_success_at = (Get-Date).ToString('o')
        profile = $Profile
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Write-SchedulerLog 'Catch-up cycle completed successfully.'
    exit 0
}
catch {
    Write-SchedulerLog "Runner failed: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
