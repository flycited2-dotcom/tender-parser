[CmdletBinding(SupportsShouldProcess = $true)]
param()
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$taskName = 'Tender Parser Daily'
$task = Get-ScheduledTask -TaskName $taskName
# Never stop a real collection just to clear scheduler bookkeeping.
$collectors = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match '(?i)-m\s+tender_parser\s+run\b'
}
if ($collectors) { throw 'A collection is still running; task repair refused.' }
if ($task.Actions.Arguments -notmatch [regex]::Escape($projectDirectory)) {
    throw 'Task does not point to this project; repair refused.'
}
if ($PSCmdlet.ShouldProcess($taskName, 'Back up and reset stale task registration')) {
    $backupDirectory = Join-Path $projectDirectory 'data\maintenance'
    New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
    $backup = Join-Path $backupDirectory ('daily-task-before-repair-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.xml')
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $backup -Encoding Unicode
    Stop-ScheduledTask -TaskName $taskName
    # Windows network-profile detection can disagree with actual access under VPN.
    # Source clients already have network timeouts. Do not silently suppress a run.
    $task.Settings.RunOnlyIfNetworkAvailable = $false
    # Retain the owner's one evening trigger, no daytime catch-up or retry storm.
    $task.Settings.StartWhenAvailable = $false
    $task.Settings.RestartCount = 0
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    Write-Output "Task registration reset; previous definition: $backup"
    Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult, NextRunTime
}
