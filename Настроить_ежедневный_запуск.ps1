[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$Time = '08:00',
    [ValidateSet('full', 'fast', 'local', 'rts')]
    [string]$Profile = 'fast',
    [string]$TaskName = 'Tender Parser Daily'
)

$projectDirectory = Split-Path -Parent $PSCommandPath
$launcherPath = Join-Path $projectDirectory 'run_tender_parser_silent.bat'

if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "Silent launcher not found: $launcherPath"
}

$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument "/c `"set TENDER_PARSER_PROFILE=$Profile&& `"$launcherPath`"`""
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))
$description = "Daily tender collection and CRM queue refresh. Profile: $Profile."

if ($PSCmdlet.ShouldProcess($TaskName, "Create or update daily task at $Time")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description $description -Force | Out-Null
    Write-Host "Task '$TaskName' is scheduled daily at $Time with profile '$Profile'."
}
