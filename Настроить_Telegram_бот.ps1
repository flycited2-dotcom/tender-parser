[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = 'Tender Parser Telegram Bot')

$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project Python not found: $pythonPath"
}

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m tender_parser.telegram_bot' -WorkingDirectory $projectDirectory
$user = "$env:USERDOMAIN\$env:USERNAME"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT3M'
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

if ($PSCmdlet.ShouldProcess($TaskName, 'Create Telegram command bot task')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'Tender parser Telegram commands and report delivery.' -Force | Out-Null
    Write-Host "Task '$TaskName' starts the Telegram command bot after logon."
}
