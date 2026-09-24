param(
    [string]$TaskName = "Supplier Intelligence Sync",
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"
$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonPath = $pythonCommand.Source
$powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$launcherPath = Join-Path $PSScriptRoot "Run-ScheduledSync.ps1"
$tokenOne = Join-Path $projectDir "secrets\gmail-termoark-token.json"
$tokenTwo = Join-Path $projectDir "secrets\gmail-flycited-token.json"
if (-not (Test-Path -LiteralPath $tokenOne) -or -not (Test-Path -LiteralPath $tokenTwo)) {
    throw "Authorize both Gmail accounts with python -m tender_parser.supplier_intelligence auth --account ... first"
}
if ($IntervalMinutes -lt 10 -or $IntervalMinutes -gt 15) {
    throw "The sync interval must be between 10 and 15 minutes"
}
$launcherArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -PythonPath "{1}"' -f $launcherPath, $pythonPath
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument $launcherArguments -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
