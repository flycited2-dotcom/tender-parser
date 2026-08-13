[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$Time = '08:00',
    [ValidateSet('full', 'fast', 'local', 'rts')]
    [string]$Profile = 'fast',
    [string]$TaskName = 'Tender Parser Daily'
)

$projectDirectory = Split-Path -Parent $PSCommandPath
$runnerPath = Join-Path $projectDirectory 'run_tender_parser_resilient.ps1'

if (-not (Test-Path -LiteralPath $runnerPath)) {
    throw "Resilient runner not found: $runnerPath"
}

$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`" -Profile $Profile -ScheduleTime $Time"
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument $actionArguments
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))
$logonUser = "$env:USERDOMAIN\$env:USERNAME"
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $logonUser
$logonTrigger.Delay = 'PT2M'
$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$description = "Daily tender collection with catch-up at logon and retries. Profile: $Profile."

if ($PSCmdlet.ShouldProcess($TaskName, "Create or update daily task at $Time")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($dailyTrigger, $logonTrigger) -Principal $principal -Settings $settings -Description $description -Force | Out-Null
    Write-Host "Task '$TaskName' runs as SYSTEM daily at $Time, catches up at logon, and retries failures three times."
}
