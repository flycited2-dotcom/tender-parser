[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$Time = '02:30',
    [ValidateRange(5, 180)]
    [int]$TimeoutMinutes = 45,
    [string]$TaskName = 'Tender Parser RTS Background'
)

$projectDirectory = Split-Path -Parent $PSCommandPath
$runnerPath = Join-Path $projectDirectory 'run_rts_background.ps1'

if (-not (Test-Path -LiteralPath $runnerPath)) {
    throw "RTS background runner not found: $runnerPath"
}

$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`" -TimeoutMinutes $TimeoutMinutes"
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument $actionArguments
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ($TimeoutMinutes + 5)) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$description = "Isolated RTS-Tender refresh with an atomic last-good snapshot. The fast daily parser consumes this snapshot without waiting for RTS."

if ($PSCmdlet.ShouldProcess($TaskName, "Create or update isolated RTS task at $Time")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $dailyTrigger `
        -Settings $settings `
        -Description $description `
        -Force | Out-Null
    Write-Host "Task '$TaskName' runs daily at $Time, catches up when available, times out after $TimeoutMinutes minutes, and retries three times."
}
