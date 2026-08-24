[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = 'Tender Personal Telegram Agent')

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument '-m tender_parser.telegram_agent' `
    -WorkingDirectory $projectDirectory
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess($TaskName, 'Create personal Telegram agent task')) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description 'Personal read-only Codex tender assistant in Telegram.' `
        -Force | Out-Null
    Write-Host "Task '$TaskName' starts after user logon."
}
