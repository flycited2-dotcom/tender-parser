[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string[]]$Times = @('08:00', '11:00', '15:00', '18:00'),
    [ValidateSet('full', 'fast', 'local', 'rts')]
    [string]$Profile = 'fast'
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$runnerPath = Join-Path $projectDirectory 'run_tender_parser_resilient.ps1'
$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $runnerPath)) {
    throw "Resilient runner not found: $runnerPath"
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

foreach ($time in $Times) {
    if ($time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
        throw "Invalid schedule time: $time"
    }
    $taskName = if ($time -eq '08:00') { 'Tender Parser Daily' } else { "Tender Parser $($time.Replace(':', '-'))" }
    $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`" -Profile $Profile -ScheduleTime $time"
    $action = New-ScheduledTaskAction -Execute $powershellPath -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($time, 'HH:mm', $null))
    if ($PSCmdlet.ShouldProcess($taskName, "Create or update working-day tender run at $time")) {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description "Tender collection at $time with catch-up and retries. Profile: $Profile." `
            -Force | Out-Null
    }
}

Write-Host "Tender collection schedule: $($Times -join ', ')."
